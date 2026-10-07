"""Market-data tools — read-only, but still token-gated.

Wraps the same logic that powers the terminal UI's market widgets, orderbook,
funding compare, candles, whales, and market-config endpoints. The data itself
is public on-chain / from Perpl's public REST API, but every tool requires a
valid MCP token (with the 'read' scope) so we can rate-limit per-token and
prevent anonymous abuse of upstream RPC / Perpl API quotas.
"""
import re
from typing import Optional

from app.mcp.auth import current_user
from app.mcp.server import server
from app.utils.logger import get_logger


_WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _validate_wallet(addr: str) -> str:
    """Return the lower-cased wallet or raise ValueError."""
    if not isinstance(addr, str) or not _WALLET_RE.match(addr.strip()):
        raise ValueError("wallet must be a 0x-prefixed 40-char hex address")
    return addr.strip().lower()

logger = get_logger(__name__)


# ----- helpers ---------------------------------------------------------------

# Live market maps — mirror of the dynamic registry. chain_reader.MARKETS is
# rebuilt IN PLACE from Perpl context (apply_registry, every ws_manager poll),
# so these lookups always reflect the live market set. No hardcoded ids.


def _live_maps() -> tuple[dict[str, int], dict[int, str]]:
    from app.services.chain_reader import MARKETS
    id_to_symbol = {mid: (m.get("symbol") or f"MKT-{mid}") for mid, m in MARKETS.items()}
    symbol_to_id = {str(s).upper(): mid for mid, s in id_to_symbol.items()}
    return symbol_to_id, id_to_symbol


async def _ensure_registry() -> tuple[dict[str, int], dict[int, str]]:
    symbol_to_id, id_to_symbol = _live_maps()
    if not id_to_symbol:
        # Cold process (no ws_manager poll yet) — prime the registry once.
        from app.services import market_registry
        await market_registry.refresh_markets(force=True)
        symbol_to_id, id_to_symbol = _live_maps()
    return symbol_to_id, id_to_symbol


async def _resolve_market_id(symbol_or_id: str | int) -> int:
    """Accept either a symbol ('SOL') or a market id (31) and return the id,
    validated against the LIVE registry."""
    symbol_to_id, id_to_symbol = await _ensure_registry()
    if isinstance(symbol_or_id, int):
        if symbol_or_id not in id_to_symbol:
            raise ValueError(f"Unknown market id {symbol_or_id}. Valid: {sorted(id_to_symbol)}")
        return symbol_or_id
    s = str(symbol_or_id).strip().upper()
    if s in symbol_to_id:
        return symbol_to_id[s]
    if s.isdigit() and int(s) in id_to_symbol:
        return int(s)
    raise ValueError(f"Unknown market '{symbol_or_id}'. Valid symbols: {sorted(symbol_to_id)}")


async def _symbol_of(mid: int) -> str:
    _, id_to_symbol = await _ensure_registry()
    return id_to_symbol.get(mid, f"MKT-{mid}")


# ----- tools -----------------------------------------------------------------


@server.tool()
async def list_markets() -> list[dict]:
    """List all live perpetual futures markets on Perpl (BTC, ETH, SOL, MON
    on Monad Mainnet) with current mark price, bid/ask, open interest, 24h
    change, funding rate, and TVL. Use this as the starting point for any
    market analysis.
    """
    from app.routers.markets import _format_market
    from app.services.perpl_client import perpl_client

    await current_user(required_scope="read")
    raw = await perpl_client.get_markets()
    return [_format_market(m) for m in raw if m.get("config", {}).get("is_open", False)]


@server.tool()
async def get_market(symbol: str) -> dict:
    """Get a single market's full snapshot by symbol (BTC, ETH, SOL, MON).
    Returns mark/bid/ask prices, OI, funding rate, fees, margin requirements.
    """
    from app.routers.markets import _format_market
    from app.services.perpl_client import perpl_client

    await current_user(required_scope="read")
    mid = await _resolve_market_id(symbol)
    raw = await perpl_client.get_market(mid)
    if not raw:
        raise ValueError(f"Market {symbol} not found upstream")
    return _format_market(raw)


@server.tool()
async def get_orderbook(symbol: str, depth: int = 10) -> dict:
    """Get the on-chain orderbook for a market. Returns best `depth` bids
    and asks plus any 'whale' levels (orders >$50k notional).
    Use this to assess liquidity, spread, and large resting orders before
    proposing an entry.
    """
    from app.main import ob_cache  # background thread keeps this fresh
    await current_user(required_scope="read")
    mid = await _resolve_market_id(symbol)
    sym = await _symbol_of(mid)
    cached = ob_cache.get(mid)
    if not cached:
        return {"market_id": mid, "symbol": sym, "bids": [], "asks": [], "whales": []}
    return {
        "market_id": mid,
        "symbol": sym,
        "bids": cached.get("bids", [])[:depth],
        "asks": cached.get("asks", [])[:depth],
        "whales": cached.get("whales", []),
    }


# Perpl candle resolutions are integer SECONDS — only these values are accepted.
# Using any other value (e.g. minutes interpreted as seconds) returns 400.
_RESOLUTION_MAP: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@server.tool()
async def get_candles(symbol: str, resolution: str = "1h", hours: int = 24) -> dict:
    """Get OHLCV candles for a market. Useful for trend / volatility /
    structure analysis before proposing a trade.

    Args:
        symbol: BTC, ETH, SOL, or MON
        resolution: candle width — one of '1m', '5m', '15m', '1h', '4h', '1d'
        hours: how many hours of history to fetch (default 24)
    """
    import time as _t
    import httpx
    from app.config import settings

    await current_user(required_scope="read")
    mid = await _resolve_market_id(symbol)
    hours = max(1, min(int(hours), 720))  # cap at 30 days to prevent abuse
    res_key = resolution.lower().strip()
    if res_key not in _RESOLUTION_MAP:
        raise ValueError(
            f"Unknown resolution '{resolution}'. Valid: {list(_RESOLUTION_MAP)}"
        )
    res_seconds = _RESOLUTION_MAP[res_key]

    now_ms = int(_t.time() * 1000)
    from_ms = now_ms - hours * 3600 * 1000
    url = f"{settings.PERPL_REST_URL}/api/v1/market-data/{mid}/candles/{res_seconds}/{from_ms}-{now_ms}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(
                f"Perpl candles API returned {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
    return {
        "market_id": mid,
        "symbol": await _symbol_of(mid),
        "resolution": res_key,
        "resolution_seconds": res_seconds,
        "hours": hours,
        "candles": data,
    }


@server.tool()
async def get_market_configs() -> list[dict]:
    """Get the static configuration for every market: price/size decimals,
    initial & maintenance margin (basis points), maker/taker fees,
    maximum leverage. Useful when you need to convert sizes or compute
    liquidation prices.
    """
    from app.services.perpl_client import perpl_client

    await current_user(required_scope="read")
    ctx = await perpl_client.get_context()
    out: list[dict] = []
    for m in ctx.get("markets", []):
        mid = m.get("id")
        cfg = m.get("config", {})
        if not cfg.get("is_open", False):
            continue
        init = cfg.get("initial_margin", 0) or 0
        out.append({
            "market_id": mid,
            "symbol": m.get("name") or m.get("symbol") or f"MKT-{mid}",
            "price_decimals": cfg.get("price_decimals", 1),
            "size_decimals": cfg.get("size_decimals", 5),
            "initial_margin_bps": cfg.get("initial_margin", 0),
            "maintenance_margin_hdths": cfg.get("maintenance_margin", 2000),
            # Dynamic: max leverage = initial_margin/100 (same rule as main.py)
            "max_leverage": max(1, round(init / 100)) if init > 0 else 10,
            # raw/100 = bps (taker_fee 690 -> 6.9 bps), matching /api/market-configs
            "maker_fee_bps": cfg.get("maker_fee", 0) / 100,
            "taker_fee_bps": cfg.get("taker_fee", 0) / 100,
            "is_open": True,
        })
    return out


@server.tool()
async def get_funding_compare(symbol: Optional[str] = None) -> list[dict]:
    """Compare Perpl funding rates against Hyperliquid for the same symbols.
    Returns one entry per market with both rates and a 'signal' field
    suggesting funding-arbitrage opportunities.

    If `symbol` is given, returns just that market's row.
    """
    from app.services.ws_manager import ws_manager
    from app.services.hyperliquid_client import get_perpl_markets_funding

    await current_user(required_scope="read")
    hl_data = await get_perpl_markets_funding()
    _, id_to_symbol = await _ensure_registry()
    rows: list[dict] = []
    for mid, sym in id_to_symbol.items():
        if symbol and sym != symbol.upper():
            continue
        perpl_state = ws_manager.market_state_cache.get(mid, {}) if ws_manager else {}
        perpl_rate = perpl_state.get("funding_rate", 0)
        hl_row = hl_data.get(sym, {}) if isinstance(hl_data, dict) else {}
        hl_rate = hl_row.get("funding_rate", 0)
        diff = (perpl_rate or 0) - (hl_rate or 0)
        signal = "neutral"
        if abs(diff) > 0.0005:  # 5 bps gap
            signal = "perpl_higher" if diff > 0 else "hl_higher"
        rows.append({
            "symbol": sym,
            "perpl": {
                "funding_rate": perpl_rate,
                "rate_pct": round((perpl_rate or 0) * 100, 4),
                "mark_price": perpl_state.get("mark_price"),
            },
            "hyperliquid": {
                "funding_rate": hl_rate,
                "rate_pct": round((hl_rate or 0) * 100, 4),
                "mark_price": hl_row.get("mark_price"),
            },
            "diff_pct": round(diff * 100, 4),
            "signal": signal,
        })
    return rows


@server.tool()
async def get_funding_history(symbol: str, hours: int = 24) -> dict:
    """Historical funding rates for a market over the past `hours`.
    Useful for detecting funding regime shifts or sustained one-sided pressure.
    """
    from app.services.funding_tracker import funding_tracker

    await current_user(required_scope="read")
    mid = await _resolve_market_id(symbol)
    hours = max(1, min(int(hours), 720))
    history = funding_tracker.get_history(mid)
    # Filter to requested window
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    filtered = [
        h for h in history
        if not h.get("timestamp") or _parse_ts(h["timestamp"]) >= cutoff
    ]
    return {
        "market_id": mid,
        "symbol": await _symbol_of(mid),
        "hours": hours,
        "samples": len(filtered),
        "history": filtered,
    }


def _parse_ts(ts):
    from datetime import datetime
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow()


@server.tool()
async def get_whale_alerts(symbol: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Recent whale activity: large orders or open-interest spikes.
    Pass a `symbol` to filter to one market, or omit to see all.
    """
    from sqlalchemy import select, desc
    from app.db.database import get_session_factory
    from app.db.models import WhaleAlert

    await current_user(required_scope="read")
    mid = await _resolve_market_id(symbol) if symbol else None
    limit = max(1, min(int(limit), 100))
    sf = get_session_factory()
    async with sf() as session:
        q = select(WhaleAlert).order_by(desc(WhaleAlert.timestamp)).limit(limit)
        if mid is not None:
            q = q.where(WhaleAlert.market_id == mid)
        result = await session.execute(q)
        rows = result.scalars().all()
    _, _id_to_symbol = await _ensure_registry()
    return [
        {
            "id": r.id,
            "market_id": r.market_id,
            "symbol": _id_to_symbol.get(r.market_id, str(r.market_id)),
            "alert_type": r.alert_type,
            "side": r.side,
            "size_usd": r.size_usd,
            "price": r.price,
            "severity": r.severity,
            "details": r.details,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in rows
    ]
