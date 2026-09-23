"""CEX funding polls (doc 02 §7, prompt Part 2 · Decision 3).

Binance USDT-M `premiumIndex` and Bybit v5 `tickers` — both verified reachable
from the prod server (HTTP 200; recorded in the report). Read-only public
endpoints, polled on a cadence by feeds.py, written to strat_funding. On failure
the row is skipped and the error logged — never fabricated.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("strategy_engine.cex_funding")
_TIMEOUT = httpx.Timeout(12.0)

# coin -> venue symbol
_BINANCE_SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
_BYBIT_SYM = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


async def poll_binance(coin: str) -> Optional[dict]:
    sym = _BINANCE_SYM.get(coin)
    if not sym:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": sym})
            r.raise_for_status()
            d = r.json()
        return {
            "venue": "binance", "coin": coin, "ts": int(time.time() * 1000),
            "rate": float(d["lastFundingRate"]),
            "predicted_rate": None,
            "next_settlement_ts": int(d["nextFundingTime"]) if d.get("nextFundingTime") else None,
            "oi_notional": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("binance premiumIndex %s failed: %s", coin, exc)
        return None


async def poll_bybit(coin: str) -> Optional[dict]:
    sym = _BYBIT_SYM.get(coin)
    if not sym:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get("https://api.bybit.com/v5/market/tickers",
                                 params={"category": "linear", "symbol": sym})
            r.raise_for_status()
            d = r.json()
        lst = (d.get("result") or {}).get("list") or []
        if not lst:
            return None
        t = lst[0]
        oi_val = t.get("openInterestValue")
        return {
            "venue": "bybit", "coin": coin, "ts": int(time.time() * 1000),
            "rate": float(t["fundingRate"]) if t.get("fundingRate") not in (None, "") else None,
            "predicted_rate": None,
            "next_settlement_ts": int(t["nextFundingTime"]) if t.get("nextFundingTime") else None,
            "oi_notional": float(oi_val) if oi_val not in (None, "") else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("bybit tickers %s failed: %s", coin, exc)
        return None
