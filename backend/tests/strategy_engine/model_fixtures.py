"""Synthetic candle scenarios for the M1–M6 unit tests (docs 11–16 §Tests).

15m candles are authored explicitly; 1h / 4h are AGGREGATED from them so the
three timeframes are always consistent (the runner reads them from the same
feed). Feeds (OI / taker / book / liquidations) are minute rows the tests set."""
from __future__ import annotations

import dataclasses
import math

from app.strategy_engine.mind.snapshot import build_snapshot
from app.strategy_engine.structure.candles import Candle, DAY_MS, TF_MS, atr_value

M15, H1, H4 = TF_MS["15m"], TF_MS["1h"], TF_MS["4h"]
# Tuesday 2026-09-08 00:00 UTC — a normal mid-week day (M6 wants Mon–Thu, M5 wants a window)
TUE = 1_788_825_600_000
MON = TUE - DAY_MS


def aggregate(c15: list[Candle], tf_ms: int) -> list[Candle]:
    """Bucket 15m candles into tf candles keyed by the bucket's close time."""
    out: list[Candle] = []
    buckets: dict[int, list[Candle]] = {}
    for c in c15:
        b = ((c.ts - 1) // tf_ms + 1) * tf_ms
        buckets.setdefault(b, []).append(c)
    for b in sorted(buckets):
        cs = buckets[b]
        if len(cs) * M15 != tf_ms:       # partial bucket → not a closed candle
            continue
        out.append(Candle(b, cs[0].o, max(c.h for c in cs), min(c.l for c in cs), cs[-1].c, sum(c.v for c in cs)))
    return out


def path15(prices: list[float], start: int, spread: float = 0.4, v: float = 100.0) -> list[Candle]:
    """15m candles closing at start + (i+1)*15m, each closing at prices[i]."""
    out = []
    for i, p in enumerate(prices):
        o = prices[i - 1] if i else p
        out.append(Candle(start + (i + 1) * M15, o, max(o, p) + spread, min(o, p) - spread, p, v))
    return out


def zigzag(n: int, base: float = 100.0, amp: float = 1.5, drift: float = 0.0, period: int = 16) -> list[float]:
    return [base + drift * i + amp * math.sin(2 * math.pi * i / period) for i in range(n)]


def candle(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Candle:
    return Candle(ts, o, max(h, o, c), min(l, o, c), c, v)


def feeds(now: int, oi_start: float = 1_000_000.0, oi_slope_per_min: float = 0.0, oi_steps: dict | None = None,
          buy_ratio: float = 0.5, br_steps: dict | None = None, mid: float | None = None, span_h: int = 26):
    """Minute feeds for the last `span_h` hours. oi_steps / br_steps: {ts_from: value}
    piecewise overrides (applied to rows with ts >= ts_from)."""
    n = span_h * 60
    oi, tr = [], []
    for i in range(n):
        ts = now - (n - i) * 60_000
        val = oi_start * (1 + oi_slope_per_min * i)
        for t0, v in sorted((oi_steps or {}).items()):
            if ts >= t0:
                val = v
        br = buy_ratio
        for t0, v in sorted((br_steps or {}).items()):
            if ts >= t0:
                br = v
        oi.append({"ts": ts, "oi_notional": val, "funding": 0.0, "mark": mid or 100.0})
        tr.append({"ts": ts, "taker_buy_notional": 1000.0 * br, "taker_sell_notional": 1000.0 * (1 - br)})
    book = [{"ts": now - H1 + i * 5_000, "mid": mid or 100.0, "bid_0_1": 30_000.0, "bid_0_3": 50_000.0, "bid_0_5": 70_000.0,
             "ask_0_1": 30_000.0, "ask_0_3": 50_000.0, "ask_0_5": 70_000.0} for i in range(720)]
    return oi, tr, book


def snapshot(c15: list[Candle], now: int, *, oi_rows=None, trades_rows=None, book_rows=None, liq_rows=None,
             positions_rows=None, gauge=None, gauge_24h=None, oi_7d_high=None, cohort=None, events_ts=None,
             recent_form=None, model_state=None, day_type_override=None, coin: str = "BTC"):
    c1h, c4h = aggregate(c15, H1), aggregate(c15, H4)
    return build_snapshot(coin, now, c15, c1h, c4h, oi_rows=oi_rows or [], trades_rows=trades_rows or [],
                          book_rows=book_rows or [], liq_rows=liq_rows or [], positions_rows=positions_rows or [],
                          gauge=gauge or {}, gauge_24h=gauge_24h or [], oi_7d_high=oi_7d_high, cohort=cohort or {},
                          events_ts=events_ts or [], recent_form=recent_form or [], model_state=model_state or {},
                          day_type_override=day_type_override)


def atr15(c15: list[Candle]) -> float:
    return float(atr_value(c15) or 0.0)


def with_setup(snap, setup: dict, **kw):
    return dataclasses.replace(snap, setup=setup, **kw)
