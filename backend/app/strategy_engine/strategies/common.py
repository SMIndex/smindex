"""Helpers shared by the strategy evaluators — all pure, all computed over the
available window with warming labels (no waiting on history)."""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional

import numpy as np

from ..features import indicators as ind
from ..features import timing as tim
from .base import Cond, StrategyContext, UNAVAILABLE, avail, ohlc

_UTC = _dt.timezone.utc
_MIN = 60_000
_15M = 900_000
_H = 3_600_000
_DAY = 86_400_000

BARS_15M_PER_DAY = 96
PPY_15M = 96 * 365            # periods per year for 15m log returns


def hull_need(n: int) -> int:
    """Bars before hull_ma(n) has 2 finite slope values."""
    return n + int(round(math.sqrt(n))) + 1


def hull_dir_warm(closes: list[float], n: int) -> tuple[int, Optional[str]]:
    d = tim.hull_direction(closes, n) if closes else 0
    return d, avail(len(closes), hull_need(n), "bars")


def fisher_turn_warm(highs, lows, n: int = 9, lookback: int = 6) -> tuple[int, Optional[str]]:
    ft = tim.fisher_turn(highs, lows, n, lookback) if highs else 0
    return ft, avail(len(highs), n + lookback, "bars")


def atr_cond_value(ctx: StrategyContext, n: int = 14) -> tuple[float, Optional[str]]:
    """ATR(n) on 15m over the available window + warming label."""
    h, l, c = ohlc(ctx.candles_15m)
    a, have = ind.atr_last(h, l, c, n)
    return a, avail(have, n + 1, "bars")


def rv24h_pct(closes_15m: list[float], days: int = 30) -> tuple[float, float, int]:
    """(rv_24h, percentile vs trailing `days` daily RVs, days available) from 15m
    closes: RV24h = std of the last 96 15m log returns (annualised); history =
    one RV per prior non-overlapping day."""
    rv, _used = ind.rv_from_closes(closes_15m, BARS_15M_PER_DAY, PPY_15M)
    hist = ind.daily_rv_history(closes_15m, BARS_15M_PER_DAY, PPY_15M)
    hist = hist[:-1][-days:] if hist.size > 1 else hist[:0]     # exclude the current day
    if not np.isfinite(rv) or hist.size < 1:
        return rv, float("nan"), int(hist.size)
    pct = float((hist <= rv).sum() / hist.size * 100.0)
    return rv, pct, int(hist.size)


def rv_1h_vs_24h(closes_15m: list[float]) -> tuple[Optional[float], Optional[str]]:
    """Realized vol over the last 1h (4 bars) vs the 24h average (96 bars) as a
    ratio; warming below 96 bars."""
    r = ind.log_returns(closes_15m)
    r = r[np.isfinite(r)]
    if r.size < 5:
        return None, avail(int(r.size), BARS_15M_PER_DAY, "bars")
    last = float(np.std(r[-4:], ddof=0))
    day = float(np.std(r[-BARS_15M_PER_DAY:], ddof=0))
    if day <= 0:
        return None, avail(int(r.size), BARS_15M_PER_DAY, "bars")
    return last / day, avail(int(r.size), BARS_15M_PER_DAY, "bars")


def taker_buckets_15m(trades_1m: list[dict], candle_ts: list[int]) -> list[Optional[float]]:
    """Total taker notional per 15m candle (candle ts = close time; bucket =
    (ts-15m, ts]). None for candles with no 1m rows."""
    out: list[Optional[float]] = []
    by_min = {int(t["ts"]): float(t["taker_buy_notional"] or 0) + float(t["taker_sell_notional"] or 0)
              for t in trades_1m}
    for cts in candle_ts:
        tot, n = 0.0, 0
        for m in range(int(cts) - _15M + _MIN, int(cts) + 1, _MIN):
            v = by_min.get(m - (m % _MIN))
            if v is not None:
                tot += v
                n += 1
        out.append(tot if n else None)
    return out


def volume_cond(ctx: StrategyContext, mult: float = 1.3, n_avg: int = 20) -> tuple[Cond, Optional[bool]]:
    """Break-candle taker vol >= mult x n_avg-candle average (doc 05)."""
    c = ctx.candles_15m
    if not c or not ctx.trades_1m:
        return Cond("Volume", f"break-candle taker vol >= {mult}x {n_avg}-candle avg", value="no 1m taker tape",
                    warming=UNAVAILABLE), None
    buckets = taker_buckets_15m(ctx.trades_1m, [x["ts"] for x in c[-(n_avg + 1):]])
    last = buckets[-1]
    prior = [b for b in buckets[:-1] if b is not None]
    if last is None or not prior:
        return Cond("Volume", f"break-candle taker vol >= {mult}x {n_avg}-candle avg",
                    value="no taker rows in break candle", warming=avail(len(prior), n_avg, "candles")), None
    avg = sum(prior) / len(prior)
    ok = avg > 0 and last >= mult * avg
    return Cond("Volume", f"break-candle taker vol >= {mult}x {n_avg}-candle avg",
                value=(f"{last / avg:.2f}x avg" if avg > 0 else "avg 0"), threshold=f"need {mult}x",
                met=ok, warming=avail(len(prior), n_avg, "candles")), ok


def crowd_cond(ctx: StrategyContext, direction: Optional[str]) -> tuple[Cond, Optional[bool]]:
    """Funding gauge not EXTREME on the trade side (doc 02 gauge)."""
    g = ctx.gauge or {}
    level = g.get("crowding_level")
    z = g.get("funding_z")
    warm = None
    if not ctx.gauge:
        warm = UNAVAILABLE
    elif level == "insufficient_data":
        warm = avail(int(ctx.funding_rows), 720, "funding rows")
    ok = None
    if direction and ctx.gauge:
        ok = not (level == "EXTREME" and g.get("blocked_direction") == direction)
    val = (level or "n/a") + (f" (z={float(z):+.2f})" if z is not None else "")
    return Cond("Not crowded", "funding gauge not EXTREME on the trade side", value=val, met=ok, warming=warm), ok


def timing_cond(ctx: StrategyContext, direction: Optional[str], hull_n_1h: int = 21) -> tuple[Cond, Optional[float], int, int]:
    """Timing layer (doc 06 §8): 1h Hull(21) direction + 15m Fisher(9) turn →
    timing_score 1.0/0.6/0.2/0. Returns (cond, score, hull_dir, fisher_turn)."""
    h, l, _c = ohlc(ctx.candles_15m)
    hd, w1 = hull_dir_warm([float(x["c"]) for x in ctx.candles_1h], hull_n_1h)
    ft, w2 = fisher_turn_warm(h, l, 9)
    score = None
    if direction:
        score = tim.timing_score(hd, ft, 1 if direction == "long" else -1)
    warm = w1 or w2
    if w1 == UNAVAILABLE or w2 == UNAVAILABLE:
        warm = UNAVAILABLE
    return (Cond("Timing", "1h Hull(21) direction + 15m Fisher(9) turn → 1.0/0.6/0.2/0",
                 value=f"hull1h={hd:+d} fisher15m={ft:+d}" + (f" → {score:.1f}" if score is not None else ""),
                 met=(None if score is None else score >= 0.6), warming=warm), score, hd, ft)


def bars_1m_from_book(book_5s: list[dict], minutes: int) -> list[dict]:
    """Aggregate the 5s mid tape into 1-minute bars {ts,o,h,l,c} (most recent
    `minutes`)."""
    bars: dict[int, dict] = {}
    for r in book_5s:
        m = int(r["ts"]) // _MIN * _MIN
        mid = float(r["mid"])
        b = bars.get(m)
        if b is None:
            bars[m] = {"ts": m, "o": mid, "h": mid, "l": mid, "c": mid}
        else:
            b["h"] = max(b["h"], mid); b["l"] = min(b["l"], mid); b["c"] = mid
    keys = sorted(bars)[-minutes:]
    return [bars[k] for k in keys]


def bars_5m_from_1m(bars_1m: list[dict]) -> list[dict]:
    out: dict[int, dict] = {}
    for b in bars_1m:
        k = int(b["ts"]) // (5 * _MIN) * (5 * _MIN)
        o = out.get(k)
        if o is None:
            out[k] = {"ts": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}
        else:
            o["h"] = max(o["h"], b["h"]); o["l"] = min(o["l"], b["l"]); o["c"] = b["c"]
    return [out[k] for k in sorted(out)]


def oi_change(oi_1m: list[dict], span_ms: int, now_ms: int) -> tuple[Optional[float], int, Optional[str]]:
    """Fractional OI change over `span_ms` using the OI tape that exists:
    (oi_now / oi_at_or_before(now-span) - 1, minutes_available, warming). When the
    tape is shorter than the span the oldest row is the base (labelled)."""
    rows = [r for r in oi_1m if r.get("oi_notional") is not None]
    need = int(span_ms // _MIN)
    if not rows:
        return None, 0, UNAVAILABLE
    now_oi = float(rows[-1]["oi_notional"])
    base_ts = now_ms - span_ms
    base = None
    for r in rows:
        if int(r["ts"]) <= base_ts:
            base = r
        else:
            break
    if base is None:
        base = rows[0]
    have = int((int(rows[-1]["ts"]) - int(rows[0]["ts"])) // _MIN) + 1
    b = float(base["oi_notional"])
    if b <= 0:
        return None, have, avail(have, need, "min of OI")
    return now_oi / b - 1.0, have, avail(have, need, "min of OI")


def price_change(oi_1m: list[dict], span_ms: int, now_ms: int) -> Optional[float]:
    rows = [r for r in oi_1m if r.get("mark") is not None]
    if not rows:
        return None
    base_ts = now_ms - span_ms
    base = rows[0]
    for r in rows:
        if int(r["ts"]) <= base_ts:
            base = r
        else:
            break
    b = float(base["mark"])
    return (float(rows[-1]["mark"]) / b - 1.0) if b > 0 else None


def utc_day_start(ts_ms: int) -> int:
    return int(ts_ms // _DAY * _DAY)


def fmt_px(px: float) -> str:
    return f"{px:,.2f}" if px < 1000 else f"{px:,.1f}"


def ago(ms: int) -> str:
    if ms < 60_000:
        return f"{ms // 1000}s"
    if ms < _H:
        return f"{ms // 60_000}m"
    return f"{ms / _H:.1f}h"
