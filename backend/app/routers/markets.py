import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.services.perpl_client import perpl_client
from app.services import market_registry
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/markets", tags=["markets"])

# Simple in-memory cache for the price-bearing list (terminal polls this).
_markets_cache: dict[str, Any] = {"data": None, "timestamp": 0.0, "fetched_iso": None}
_CACHE_TTL = 5.0  # seconds


def _format_market(m: dict) -> dict:
    config = m.get("config", {})
    state = m.get("state", {})
    price_dec = config.get("price_decimals", 1)
    size_dec = config.get("size_decimals", 5)
    pd = 10 ** price_dec
    sd = 10 ** size_dec

    mark_price = state.get("mrk", 0) / pd if state.get("mrk") else 0
    last_price = state.get("lst", 0) / pd if state.get("lst") else 0
    prev_price = state.get("prv", 0) / pd if state.get("prv") else 0
    oi = state.get("oi", 0) / sd if state.get("oi") else 0
    dv = state.get("dv", 0) / sd if state.get("dv") else 0

    funding = m.get("funding", {})
    funding_rate = funding.get("rate", 0) / 10000 if funding.get("rate") else 0

    tvl = int(state.get("tvl", 0)) / 1e6 if state.get("tvl") else 0
    dva = int(state.get("dva", 0)) / 1e6 if state.get("dva") else 0
    oi_usd = oi * mark_price if mark_price else 0

    # Perpl puts the ticker in `name` (symbol is "" for BTC/MON).
    symbol = m.get("name") or m.get("symbol") or f"MKT-{m.get('id')}"
    name = m.get("name") or m.get("symbol") or f"Market {m.get('id')}"
    price_round = price_dec

    return {
        "id": m.get("id"),
        "market_id": m.get("id"),           # alias for registry consumers
        "symbol": symbol,
        "name": name,
        "price_decimals": price_dec,        # from live config (no hardcoded fallback)
        "size_decimals": size_dec,
        "mark_price": round(mark_price, price_round),
        "last_price": round(last_price, price_round),
        "bid_price": round(state.get("bid", 0) / pd, price_round) if state.get("bid") else 0,
        "ask_price": round(state.get("ask", 0) / pd, price_round) if state.get("ask") else 0,
        "prev_price": round(prev_price, price_round),
        "open_interest": round(oi, 6),
        "open_interest_usd": round(oi_usd, 2),
        "daily_volume": round(dv, 6),
        "daily_volume_usd": round(dva, 2),
        "tvl": round(tvl, 2),
        "price_change_24h": round(((mark_price - prev_price) / prev_price * 100) if prev_price else 0, 2),
        "is_open": config.get("is_open", False),
        "is_active": config.get("is_open", False),  # alias
        # WS-parity fields — the 60s REST re-seed must not drop what /ws/feed carries
        "oracle_price": round(state.get("orl", 0) / pd, price_round) if state.get("orl") else 0,
        "mid_price": round(state.get("mid", 0) / pd, price_round) if state.get("mid") else 0,
        "initial_margin": config.get("initial_margin", 0) / 10000,
        "maintenance_margin": config.get("maintenance_margin", 0) / 10000,
        # raw/100 = bps (taker_fee 690 -> 6.9 bps) — same scaling as /api/market-configs
        "maker_fee": config.get("maker_fee", 0) / 100,
        "taker_fee": config.get("taker_fee", 0) / 100,
        "funding_rate": funding_rate,
    }


@router.get("")
@router.get("/")
async def list_markets() -> dict:
    """Active Perpl markets (dynamic from context) with live state, in a registry
    envelope. `markets` is a superset: price fields (terminal) + config/decimals
    (registry consumers). Markets removed by Perpl disappear automatically."""
    now = time.monotonic()
    if _markets_cache["data"] is not None and now - _markets_cache["timestamp"] < _CACHE_TTL:
        data = _markets_cache["data"]
        fetched = _markets_cache["fetched_iso"]
    else:
        try:
            raw_markets = await perpl_client.get_markets()
            data = [
                _format_market(m) for m in raw_markets
                if m.get("config", {}).get("is_open", False)
            ]
            _markets_cache["data"] = data
            _markets_cache["timestamp"] = now
            _markets_cache["fetched_iso"] = datetime.now(timezone.utc).isoformat()
            fetched = _markets_cache["fetched_iso"]
        except Exception as exc:
            logger.error("Failed to fetch markets: %s", exc)
            if _markets_cache["data"] is not None:
                data = _markets_cache["data"]
                fetched = _markets_cache["fetched_iso"]
            else:
                raise HTTPException(status_code=502, detail="Failed to fetch markets from Perpl")

    return {
        "markets": data,
        "source": market_registry.SOURCE,
        "fetched_at": fetched,
        "ttl_seconds": int(_CACHE_TTL),
    }


@router.get("/symbol/{symbol}")
async def get_market_by_symbol(symbol: str) -> dict:
    m = await market_registry.get_market_by_symbol(symbol)
    if not m:
        raise HTTPException(status_code=404, detail=f"Market '{symbol}' not found or inactive")
    return m


@router.post("/refresh")
async def refresh_markets() -> dict:
    """Force a registry refresh from Perpl context (just clears the cache + refetches;
    no side effects beyond reloading market data)."""
    markets = await market_registry.refresh_markets(force=True)
    active = [m for m in markets if m["is_active"]]
    return {"refreshed": True, "active_count": len(active), **market_registry.cache_meta()}


@router.get("/{market_id}")
async def get_market(market_id: int) -> dict:
    try:
        raw = await perpl_client.get_market(market_id)
        if not raw:
            raise HTTPException(status_code=404, detail=f"Market {market_id} not found")
        return _format_market(raw)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch market %d: %s", market_id, exc)
        raise HTTPException(status_code=502, detail=f"Failed to fetch market {market_id}")
