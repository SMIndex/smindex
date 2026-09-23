"""Hyperliquid API client for funding rate data.

No auth required — fully public API.
Endpoint: POST https://api.hyperliquid.xyz/info
"""
import asyncio
import time
from datetime import datetime

from app.services.hyperliquid import client as hl_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

HL_API = "https://api.hyperliquid.xyz/info"

# Map Perpl market symbols to Hyperliquid coin names
PERPL_TO_HL = {
    "BTC": "BTC",
    "ETH": "ETH",
    "SOL": "SOL",
    "MON": "MON",  # May not exist on HL — will return None
}

# Cache funding data (refresh every 60s)
_cache: dict = {}
_cache_ts: float = 0
CACHE_TTL = 60


async def get_all_funding_rates() -> dict[str, dict]:
    """Get current funding rates for all HL markets.
    Returns: {symbol: {funding_rate, mark_price, open_interest, ...}}
    """
    global _cache, _cache_ts

    now = time.time()
    if _cache and now - _cache_ts < CACHE_TTL:
        return _cache

    try:
        resp = await hl_client.post_info({"type": "metaAndAssetCtxs"},
                                         priority=hl_client.BACKGROUND, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        # data is [meta, [assetCtx, ...]]
        if not isinstance(data, list) or len(data) < 2:
            return _cache

        meta = data[0]
        asset_ctxs = data[1]
        universe = meta.get("universe", [])

        result = {}
        for i, asset in enumerate(universe):
            symbol = asset.get("name", "")
            if i < len(asset_ctxs):
                ctx = asset_ctxs[i]
                funding = ctx.get("funding", "0")
                mark_price = ctx.get("markPx", "0")
                oi = ctx.get("openInterest", "0")
                volume = ctx.get("dayNtlVlm", "0")
                prev_day_px = ctx.get("prevDayPx", "0")

                try:
                    mark = float(mark_price)
                    prev = float(prev_day_px)
                    change_24h = ((mark - prev) / prev * 100) if prev > 0 else 0
                except (ValueError, ZeroDivisionError):
                    change_24h = 0

                result[symbol] = {
                    "symbol": symbol,
                    "funding_rate": float(funding) if funding else 0,
                    "mark_price": float(mark_price) if mark_price else 0,
                    "open_interest": float(oi) if oi else 0,
                    "volume_24h": float(volume) if volume else 0,
                    "change_24h": round(change_24h, 2),
                }

        _cache = result
        _cache_ts = now
        logger.debug("HL funding rates fetched: %d markets", len(result))
        return result

    except Exception:
        logger.exception("Failed to fetch Hyperliquid funding rates")
        return _cache


async def get_funding_history(coin: str, hours: int = 72) -> list[dict]:
    """Get historical funding rates for a specific coin.
    Returns list of {time, funding_rate, premium}
    """
    try:
        start_ms = int((time.time() - hours * 3600) * 1000)

        resp = await hl_client.post_info({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_ms,
        }, priority=hl_client.BACKGROUND, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        return [
            {
                "time": entry.get("time", 0),
                "timestamp": datetime.utcfromtimestamp(entry["time"] / 1000).isoformat() if entry.get("time") else None,
                "funding_rate": float(entry.get("fundingRate", "0")),
                "premium": float(entry.get("premium", "0")),
                "coin": entry.get("coin", coin),
            }
            for entry in data
        ]
    except Exception:
        logger.exception("Failed to fetch HL funding history for %s", coin)
        return []


async def get_order_book(coin: str, whale_threshold_usd: float = 500) -> dict:
    """Get HL L2 order book — returns top bids/asks with whale detection.
    Returns: {bids: [...], asks: [...], whales: [...]}
    """
    try:
        resp = await hl_client.post_info({
            "type": "l2Book",
            "coin": coin,
        }, priority=hl_client.BACKGROUND, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()

        levels = data.get("levels", [[], []])
        bids_raw = levels[0] if len(levels) > 0 else []
        asks_raw = levels[1] if len(levels) > 1 else []

        bids, asks, whales = [], [], []

        for b in bids_raw:
            price = float(b.get("px", "0"))
            size = float(b.get("sz", "0"))
            notional = price * size
            entry = {"price": price, "size": size, "notional": round(notional, 2), "side": "bid"}
            bids.append(entry)
            if notional >= whale_threshold_usd:
                whales.append({**entry, "source": "HL"})

        for a in asks_raw:
            price = float(a.get("px", "0"))
            size = float(a.get("sz", "0"))
            notional = price * size
            entry = {"price": price, "size": size, "notional": round(notional, 2), "side": "ask"}
            asks.append(entry)
            if notional >= whale_threshold_usd:
                whales.append({**entry, "source": "HL"})

        return {"bids": bids, "asks": asks, "whales": whales}

    except Exception:
        logger.exception("Failed to fetch HL order book for %s", coin)
        return {"bids": [], "asks": [], "whales": []}


async def get_perpl_markets_funding() -> dict[str, dict | None]:
    """Get HL funding rates only for Perpl-listed markets.
    Returns: {perpl_symbol: hl_data_or_none}
    """
    all_rates = await get_all_funding_rates()
    result = {}
    for perpl_sym, hl_sym in PERPL_TO_HL.items():
        result[perpl_sym] = all_rates.get(hl_sym)
    return result
