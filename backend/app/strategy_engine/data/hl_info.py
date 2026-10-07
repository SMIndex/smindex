"""Hyperliquid REST info helpers (doc 00 §3). Async httpx, read-only, no key.

Used for backfills (candleSnapshot, fundingHistory) and the 60 s
metaAndAssetCtxs fallback when the activeAssetCtx ws is quiet. Every call is
counted by the caller against the request budget (feeds.py).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

INFO_URL = "https://api.hyperliquid.xyz/info"
logger = logging.getLogger("strategy_engine.hl_info")

_TIMEOUT = httpx.Timeout(15.0)


async def _post(payload: dict) -> Optional[object]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(INFO_URL, json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("HL info %s failed: %s", payload.get("type"), exc)
        return None


async def candle_snapshot(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    """15m/1h/4h OHLCV backfill. Returns [] on failure (recorded, never faked)."""
    data = await _post({"type": "candleSnapshot",
                        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}})
    out = []
    for c in (data or []):
        out.append({"ts": int(c["t"]), "close_ts": int(c["T"]), "o": float(c["o"]), "h": float(c["h"]),
                    "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])})
    return out


async def meta_and_asset_ctxs() -> Optional[tuple[list, list]]:
    """(universe, ctxs) from metaAndAssetCtxs — fallback for the OI/funding feed."""
    data = await _post({"type": "metaAndAssetCtxs"})
    if not data or len(data) < 2:
        return None
    universe = data[0].get("universe", [])
    ctxs = data[1]
    return universe, ctxs


async def funding_history(coin: str, start_ms: int, end_ms: Optional[int] = None) -> list[dict]:
    """30-day HL funding backfill. Returns [] on failure."""
    payload = {"type": "fundingHistory", "coin": coin, "startTime": start_ms}
    if end_ms:
        payload["endTime"] = end_ms
    data = await _post(payload)
    out = []
    for f in (data or []):
        out.append({"ts": int(f["time"]), "rate": float(f["fundingRate"]),
                    "premium": (float(f["premium"]) if f.get("premium") is not None else None)})
    return out


def now_ms() -> int:
    return int(time.time() * 1000)
