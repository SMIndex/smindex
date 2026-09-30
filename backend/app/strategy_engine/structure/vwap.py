from __future__ import annotations

import math
from typing import Optional

from .candles import Candle, DAY_MS
from .sessions import day_start, candles_between, session_bounds

PROFILE_BIN_PCT = 0.0005      # 0.05% bins
VALUE_AREA = 0.70


def vwap(candles: list[Candle]) -> Optional[dict]:
    """Volume-weighted typical price with 1σ/2σ bands."""
    tv = sum(c.v for c in candles)
    if not candles or tv <= 0:
        return None
    tps = [(c.h + c.l + c.c) / 3.0 for c in candles]
    vw = sum(tp * c.v for tp, c in zip(tps, candles)) / tv
    var = sum(c.v * (tp - vw) ** 2 for tp, c in zip(tps, candles)) / tv
    sd = math.sqrt(var) if var > 0 else 0.0
    return {"vwap": vw, "sd": sd, "upper1": vw + sd, "lower1": vw - sd, "upper2": vw + 2 * sd, "lower2": vw - 2 * sd,
            "n": len(candles)}


def daily_vwap(c15: list[Candle], now_ms: int) -> Optional[dict]:
    return vwap(candles_between(c15, day_start(now_ms), now_ms))


def session_vwap(c15: list[Candle], now_ms: int, session: str) -> Optional[dict]:
    a, b = session_bounds(day_start(now_ms), session)
    return vwap(candles_between(c15, a, min(b, now_ms)))


def volume_profile(candles: list[Candle]) -> Optional[dict]:
    """0.05% price bins; each candle's volume spread uniformly over the bins it
    spans. POC = max bin; value area = 70% of volume expanding from the POC."""
    if not candles:
        return None
    ref = candles[0].c
    if ref <= 0:
        return None
    width = ref * PROFILE_BIN_PCT
    bins: dict[int, float] = {}
    for c in candles:
        if c.v <= 0:
            continue
        b0, b1 = int(c.l // width), int(c.h // width)
        n = b1 - b0 + 1
        share = c.v / n
        for b in range(b0, b1 + 1):
            bins[b] = bins.get(b, 0.0) + share
    if not bins:
        return None
    total = sum(bins.values())
    poc_b = max(bins, key=lambda b: bins[b])
    lo = hi = poc_b
    acc = bins[poc_b]
    keys = sorted(bins)
    while acc < VALUE_AREA * total:
        up = bins.get(hi + 1, 0.0) if hi + 1 <= keys[-1] else None
        dn = bins.get(lo - 1, 0.0) if lo - 1 >= keys[0] else None
        if up is None and dn is None:
            break
        if dn is None or (up is not None and up >= dn):
            hi += 1
            acc += up
        else:
            lo -= 1
            acc += dn
    return {"poc": (poc_b + 0.5) * width, "vah": (hi + 1) * width, "val": lo * width, "volume": total}


def profiles(c15: list[Candle], now_ms: int) -> dict:
    ds = day_start(now_ms)
    return {"today": volume_profile(candles_between(c15, ds, now_ms)),
            "prior_day": volume_profile(candles_between(c15, ds - DAY_MS, ds))}
