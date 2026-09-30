"""Replay-only backfill from Binance USDT-M perpetuals (spec v1.1 Part D, D-67).

Writes ONLY to strat_replay_candles / strat_replay_oi (source='binance');
never to the live strat_* feed tables.

  GET https://fapi.binance.com/fapi/v1/klines?symbol&interval&startTime&endTime&limit=1500
      [openTime, o, h, l, c, v, closeTime, quoteVol, trades, takerBuyBase, takerBuyQuote, ignore]
  GET https://fapi.binance.com/futures/data/openInterestHist?symbol&period=5m&startTime&endTime&limit=500
      [{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}] — Binance serves
      only the most recent 30 days of this series (documented limit).

Candle `ts` = closeTime (the same close-time convention as strat_candles).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

import httpx
from sqlalchemy import text

logger = logging.getLogger("strategy_engine.binance_replay")

FAPI = "https://fapi.binance.com"
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
TF_INTERVAL = {"15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
DAY_MS = 86_400_000
KLINE_LIMIT = 1500
OI_LIMIT = 500
OI_PERIOD = "5m"
OI_PERIOD_MS = 300_000
OI_MAX_DAYS = 30                       # Binance keeps 30 days of openInterestHist
_TIMEOUT = 30.0


async def _get(client: httpx.AsyncClient, path: str, params: dict):
    for attempt in range(6):
        r = await client.get(path, params=params)
        if r.status_code in (418, 429):
            ra = r.headers.get("Retry-After")
            delay = float(ra) if ra and ra.isdigit() else 5.0 * (2 ** attempt)
            logger.warning("Binance %s on %s — sleeping %.0fs", r.status_code, path, delay)
            await asyncio.sleep(delay)
            continue
        if r.status_code >= 500:
            await asyncio.sleep(2.0 * (2 ** attempt))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"Binance: {path} failed after retries")


async def backfill_candles(session, coins: list[str], tfs: list[str], start_ms: int, end_ms: int,
                           client: Optional[httpx.AsyncClient] = None) -> dict:
    """Inserts closed klines in [start, end] not yet present. Returns {coin: {tf: inserted}}."""
    own = client is None
    client = client or httpx.AsyncClient(base_url=FAPI, timeout=_TIMEOUT)
    report: dict = {}
    try:
        for coin in coins:
            sym = SYMBOLS[coin]
            report[coin] = {}
            for tf in tfs:
                have = {int(r[0]) for r in (await session.execute(text(
                    "SELECT ts FROM strat_replay_candles WHERE source='binance' AND coin=:c AND tf=:tf AND ts>=:a AND ts<=:b"),
                    {"c": coin, "tf": tf, "a": start_ms, "b": end_ms})).all()}
                ins = 0
                a = start_ms
                while a < end_ms:
                    data = await _get(client, "/fapi/v1/klines", {"symbol": sym, "interval": TF_INTERVAL[tf],
                                                                  "startTime": a, "endTime": end_ms, "limit": KLINE_LIMIT})
                    if not data:
                        break
                    rows = []
                    for k in data:
                        close_ts = int(k[6])
                        if close_ts > end_ms or close_ts in have:
                            continue
                        rows.append({"source": "binance", "coin": coin, "tf": tf, "ts": close_ts,
                                     "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
                        have.add(close_ts)
                    if rows:
                        await session.execute(text(
                            "INSERT IGNORE INTO strat_replay_candles (source, coin, tf, ts, o, h, l, c, v) "
                            "VALUES (:source, :coin, :tf, :ts, :o, :h, :l, :c, :v)"), rows)
                        ins += len(rows)
                    last_open = int(data[-1][0])
                    if len(data) < KLINE_LIMIT and last_open + TF_MS[tf] >= end_ms:
                        break
                    a = last_open + TF_MS[tf]
                    await asyncio.sleep(0.15)
                await session.commit()
                report[coin][tf] = ins
                logger.info("binance replay candles %s %s: +%d rows", coin, tf, ins)
    finally:
        if own:
            await client.aclose()
    return report


async def backfill_oi(session, coins: list[str], start_ms: int, end_ms: int,
                      client: Optional[httpx.AsyncClient] = None) -> dict:
    """5-minute openInterestHist (30-day public limit). Returns {coin: {inserted, first_ts, last_ts}}."""
    own = client is None
    client = client or httpx.AsyncClient(base_url=FAPI, timeout=_TIMEOUT)
    report: dict = {}
    try:
        for coin in coins:
            sym = SYMBOLS[coin]
            a = max(start_ms, end_ms - OI_MAX_DAYS * DAY_MS)
            have = {int(r[0]) for r in (await session.execute(text(
                "SELECT ts FROM strat_replay_oi WHERE source='binance' AND coin=:c AND ts>=:a AND ts<=:b"),
                {"c": coin, "a": a, "b": end_ms})).all()}
            ins, first, last = 0, None, None
            while a < end_ms:
                data = await _get(client, "/futures/data/openInterestHist",
                                  {"symbol": sym, "period": OI_PERIOD, "startTime": a,
                                   "endTime": min(a + OI_LIMIT * OI_PERIOD_MS, end_ms), "limit": OI_LIMIT})
                if not data:
                    a += OI_LIMIT * OI_PERIOD_MS
                    continue
                rows = []
                for d in data:
                    ts = int(d["timestamp"])
                    if ts in have:
                        continue
                    oc = float(d["sumOpenInterest"])
                    ov = float(d["sumOpenInterestValue"])
                    rows.append({"source": "binance", "coin": coin, "ts": ts, "oi_contracts": oc, "oi_notional": ov,
                                 "mark": (ov / oc) if oc > 0 else None})
                    have.add(ts)
                    first = ts if first is None else min(first, ts)
                    last = ts if last is None else max(last, ts)
                if rows:
                    await session.execute(text(
                        "INSERT IGNORE INTO strat_replay_oi (source, coin, ts, oi_contracts, oi_notional, mark) "
                        "VALUES (:source, :coin, :ts, :oi_contracts, :oi_notional, :mark)"), rows)
                    ins += len(rows)
                a = int(data[-1]["timestamp"]) + OI_PERIOD_MS
                await asyncio.sleep(0.15)
            await session.commit()
            report[coin] = {"inserted": ins, "first_ts": first, "last_ts": last, "source": "binance openInterestHist 5m"}
            logger.info("binance replay OI %s: +%d rows", coin, ins)
    finally:
        if own:
            await client.aclose()
    return report


def utc(ms: Optional[int]) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ms else "-"
