from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from .candles import Candle, MIN_MS
from .sessions import day_start, candles_between

DAY_TYPES = ("event", "squeeze", "trend_up", "trend_down", "no_trade", "range")


@dataclass
class DayInputs:
    event_within_2h: bool = False
    event_within_30m: bool = False
    funding_z: Optional[float] = None
    price_move_2h: Optional[float] = None       # signed absolute price change over the last 2h
    atr_1h: Optional[float] = None
    oi_change_2h: Optional[float] = None        # fraction (0.02 = +2%)
    oi_change_4h: Optional[float] = None
    daily_open: Optional[float] = None
    prior_vah: Optional[float] = None
    prior_val: Optional[float] = None
    bos_1h_up_today: int = 0
    bos_1h_down_today: int = 0
    delta_skew_2h: Optional[float] = None       # taker buy ratio over the last 2h (0..1)
    trend_1h: str = "range"
    rv_pct: Optional[float] = None              # realized-vol percentile 0..100
    vol_2h_ratio: Optional[float] = None        # last-2h volume / 20-day avg for this time of day

    def as_dict(self) -> dict:
        return asdict(self)


def classify(x: DayInputs) -> str:
    """doc 10 §3, evaluated in the listed order; `range` when nothing else holds."""
    if x.event_within_2h or x.event_within_30m:
        return "event"
    if (x.funding_z is not None and abs(x.funding_z) >= 2.0 and x.price_move_2h is not None and x.atr_1h
            and x.oi_change_2h is not None and x.oi_change_2h <= -0.02):
        crowd_long = x.funding_z > 0
        against = (x.price_move_2h <= -1.5 * x.atr_1h) if crowd_long else (x.price_move_2h >= 1.5 * x.atr_1h)
        if against:
            return "squeeze"
    open_above = (x.daily_open is not None and x.prior_vah is not None and x.daily_open > x.prior_vah)
    open_below = (x.daily_open is not None and x.prior_val is not None and x.daily_open < x.prior_val)
    if ((open_above or x.bos_1h_up_today >= 2) and x.oi_change_4h is not None and x.oi_change_4h >= 0.01
            and x.delta_skew_2h is not None and x.delta_skew_2h >= 0.60 and x.trend_1h == "up"):
        return "trend_up"
    if ((open_below or x.bos_1h_down_today >= 2) and x.oi_change_4h is not None and x.oi_change_4h >= 0.01
            and x.delta_skew_2h is not None and x.delta_skew_2h <= 0.40 and x.trend_1h == "down"):
        return "trend_down"
    if (x.rv_pct is not None and x.rv_pct < 25 and x.vol_2h_ratio is not None and x.vol_2h_ratio < 0.5):
        return "no_trade"
    return "range"


def volume_2h_ratio(c15: list[Candle], now_ms: int, days: int = 20) -> Optional[float]:
    """Volume of the last 8 closed 15m candles vs the average of the same 8 slots
    over the prior `days` days (only days with all 8 slots count)."""
    # Anchor the 2h window on the last closed 15m boundary: the worker evaluates a few
    # seconds after the boundary, and an unaligned start dropped the 8th candle so
    # the ratio was always None (audit D-62).
    end_ms = (now_ms // (15 * MIN_MS)) * 15 * MIN_MS
    last = candles_between(c15, end_ms - 120 * MIN_MS, end_ms)
    if len(last) < 8:
        return None
    cur = sum(c.v for c in last)
    ref = []
    for d in range(1, days + 1):
        off = d * 24 * 60 * MIN_MS
        w = candles_between(c15, end_ms - off - 120 * MIN_MS, end_ms - off)
        if len(w) == 8:
            ref.append(sum(c.v for c in w))
    if len(ref) < 5:
        return None
    avg = sum(ref) / len(ref)
    return cur / avg if avg > 0 else None


def bos_today(events, now_ms: int, direction: str) -> int:
    ds = day_start(now_ms)
    return sum(1 for e in events if e.type == "BOS" and e.direction == direction and e.ts >= ds)
