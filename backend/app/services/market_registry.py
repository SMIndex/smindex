"""Dynamic Perpl market registry — single source of truth for which markets are
ACTIVE and their config (decimals/symbol/margins).

Markets are fetched live from the Perpl public context (the same source the rest of
the app already uses) and cached with a short TTL. When Perpl removes a market (e.g.
SOL) it disappears from the context and therefore from this registry; when Perpl adds
one (e.g. HYPE) it appears automatically — no code change needed.

This module NEVER hardcodes the market list. Consumers (copy validation, risk engine,
market routes) should ask the registry instead of hardcoding {1,10,20,30}/symbols.

Note: the Perpl context puts the ticker in `name` ("" `symbol` for BTC/MON), so we
prefer `name`. `max_leverage` is intentionally NOT derived here (the context
initial_margin does not map cleanly to enforced leverage); leverage caps come from
/api/market-configs (MARKET_CONFIGS) + the per-subscription/global cap. We keep the
full raw config so nothing is invented.
"""
import time
from datetime import datetime, timezone

from app.services.perpl_client import perpl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

TTL_SECONDS = 60
SOURCE = "perpl_context"

_cache: dict = {
    "markets": [],        # normalized list (all returned markets)
    "by_id": {},          # market_id -> normalized
    "by_symbol": {},      # SYMBOL(upper) -> normalized
    "fetched_at": 0.0,    # monotonic
    "fetched_iso": None,  # wall-clock ISO
}


def _normalize(m: dict) -> dict:
    cfg = m.get("config", {}) or {}
    symbol = (m.get("name") or m.get("symbol") or f"MKT-{m.get('id')}").upper()
    return {
        "market_id": m.get("id"),
        "symbol": symbol,
        "name": m.get("name") or m.get("symbol") or symbol,
        "base_asset": symbol,
        "quote_asset": "USD",
        "decimals": cfg.get("price_decimals"),          # display decimals (compat)
        "price_decimals": cfg.get("price_decimals"),
        "size_decimals": cfg.get("size_decimals"),
        "min_order_size": cfg.get("min_posting_amount"),
        "max_leverage": None,                           # not reliable from context; see MARKET_CONFIGS
        "maintenance_margin": cfg.get("maintenance_margin"),
        "is_active": bool(cfg.get("is_open", False)),
        "raw": {"id": m.get("id"), "symbol": m.get("symbol"), "name": m.get("name"), "config": cfg},
    }


async def refresh_markets(force: bool = False) -> list[dict]:
    """Refresh the registry from Perpl context. Returns the normalized market list.
    On fetch failure, keeps and returns the last-known cache (never invents data)."""
    now = time.monotonic()
    if not force and _cache["markets"] and (now - _cache["fetched_at"] < TTL_SECONDS):
        return _cache["markets"]
    try:
        raw = await perpl_client.get_markets()
        markets = [_normalize(m) for m in raw if m.get("id") is not None]
        _cache["markets"] = markets
        _cache["by_id"] = {m["market_id"]: m for m in markets}
        _cache["by_symbol"] = {m["symbol"]: m for m in markets}
        _cache["fetched_at"] = now
        _cache["fetched_iso"] = datetime.now(timezone.utc).isoformat()
        # Keep the on-chain reader's market map in sync (position/orderbook scans).
        try:
            from app.services import chain_reader
            chain_reader.apply_registry(markets)
        except Exception:
            logger.exception("failed to sync chain_reader markets from registry")
        return markets
    except Exception as exc:
        logger.error("market_registry refresh failed: %s", exc)
        if _cache["markets"]:
            return _cache["markets"]
        raise


async def get_active_markets() -> list[dict]:
    await refresh_markets()
    return [m for m in _cache["markets"] if m["is_active"]]


async def get_all_markets() -> list[dict]:
    await refresh_markets()
    return list(_cache["markets"])


async def get_market_by_id(market_id: int) -> dict | None:
    await refresh_markets()
    return _cache["by_id"].get(market_id)


async def get_market_by_symbol(symbol: str) -> dict | None:
    await refresh_markets()
    return _cache["by_symbol"].get((symbol or "").upper())


async def is_market_active(market_id: int) -> bool:
    m = await get_market_by_id(market_id)
    return bool(m and m["is_active"])


async def get_active_market_ids() -> set[int]:
    return {m["market_id"] for m in await get_active_markets()}


async def validate_market_ids(market_ids: list[int]) -> tuple[list[int], list[int]]:
    """Split ids into (valid_active, invalid_or_inactive) against the live registry."""
    active = await get_active_market_ids()
    valid = [m for m in market_ids if m in active]
    invalid = [m for m in market_ids if m not in active]
    return valid, invalid


def cache_meta() -> dict:
    return {"source": SOURCE, "fetched_at": _cache["fetched_iso"], "ttl_seconds": TTL_SECONDS}
