from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .candles import Candle

RANGE_ATR = 1.5
BODY_MIN = 0.6


@dataclass(frozen=True)
class Displacement:
    start_index: int
    end_index: int       # inclusive; == start_index for a single candle
    direction: str       # 'up' | 'down'
    high: float
    low: float
    grade: float
    ts: int              # close time of the last candle


def grade(range_: float, body_ratio: float, atr: float) -> float:
    """displacement_grade (spec v1.1, D-64) =
    clip(0.5 * min(range/ATR - 1.5, 1) + 0.5 * min((body_ratio - 0.6)/0.3, 1), 0, 1).
    The range term is the excess over the 1.5 ATR qualifier measured in ATR
    (spec test values: 1.5 ATR / 60% -> 0.0, 2.0 ATR / 75% -> 0.5, 2.5 ATR / 90% -> 1.0)."""
    if atr <= 0:
        return 0.0
    g = 0.5 * min(range_ / atr - RANGE_ATR, 1.0) + 0.5 * min((body_ratio - BODY_MIN) / 0.3, 1.0)
    return max(0.0, min(1.0, g))


def is_displacement(c: Candle, atr: float) -> bool:
    return atr > 0 and c.range >= RANGE_ATR * atr and c.body_ratio >= BODY_MIN


def two_candle(c1: Candle, c2: Candle, atr: float) -> Optional[tuple[str, float]]:
    """2-candle sequence meeting displacement rules: same direction, combined
    range >= 1.5 ATR and combined body >= 60% of combined range."""
    if atr <= 0 or not ((c1.up and c2.up) or (c1.down and c2.down)):
        return None
    hi, lo = max(c1.h, c2.h), min(c1.l, c2.l)
    rng = hi - lo
    body = abs(c2.c - c1.o)
    if rng <= 0 or rng < RANGE_ATR * atr or body / rng < BODY_MIN:
        return None
    return ("up" if c2.up else "down"), body / rng


def detect(candles: list[Candle], atr: float, i: int) -> Optional[Displacement]:
    """Displacement ending at candle i (single candle first, else 2-candle)."""
    if i < 0 or i >= len(candles) or atr <= 0:
        return None
    c = candles[i]
    if is_displacement(c, atr):
        return Displacement(i, i, "up" if c.up else "down", c.h, c.l, grade(c.range, c.body_ratio, atr), c.ts)
    if i >= 1:
        tc = two_candle(candles[i - 1], c, atr)
        if tc:
            d, br = tc
            hi, lo = max(candles[i - 1].h, c.h), min(candles[i - 1].l, c.l)
            return Displacement(i - 1, i, d, hi, lo, grade(hi - lo, br, atr), c.ts)
    return None


LEG_MAX = 3


def leg_at(candles: list[Candle], atr: float, end: int, direction: str, length: int) -> Optional[Displacement]:
    """Displacement leg of `length` consecutive candles ending at `end`, all in
    `direction` ('up'|'down'): combined range >= 1.5 ATR and net body
    (last close - first open, signed by direction) >= 60% of the combined range.
    Grade on the leg's combined range and net body (spec v1.2, D-75)."""
    start = end - length + 1
    if atr <= 0 or start < 0 or end >= len(candles) or length < 1:
        return None
    leg = candles[start:end + 1]
    if not all((c.up if direction == "up" else c.down) for c in leg):
        return None
    hi, lo = max(c.h for c in leg), min(c.l for c in leg)
    rng = hi - lo
    body = (leg[-1].c - leg[0].o) * (1.0 if direction == "up" else -1.0)
    if rng <= 0 or rng < RANGE_ATR * atr or body / rng < BODY_MIN:
        return None
    return Displacement(start, end, direction, hi, lo, grade(rng, body / rng, atr), leg[-1].ts)


def detect_leg(candles: list[Candle], atr: float, end: int, direction: str, max_len: int = LEG_MAX) -> Optional[Displacement]:
    """Best displacement leg (highest grade) of 1..max_len consecutive candles in
    `direction` ending at `end`. M2 (doc 12) uses this for the break leg; the
    single/2-candle `detect` used by the zone builder is unchanged."""
    best = None
    for length in range(1, max_len + 1):
        d = leg_at(candles, atr, end, direction, length)
        if d is not None and (best is None or d.grade > best.grade):
            best = d
    return best


def any_against(candles: list[Candle], atr: float, direction: str, min_range_atr: float = 1.5) -> bool:
    """A candle with range >= min_range_atr ATR closing against `direction`."""
    if atr <= 0:
        return False
    for c in candles:
        if c.range >= min_range_atr * atr and ((direction == "long" and c.down) or (direction == "short" and c.up)):
            return True
    return False
