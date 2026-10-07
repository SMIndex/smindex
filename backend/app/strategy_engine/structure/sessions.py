from __future__ import annotations

import datetime as _dt
from typing import Optional

from .candles import Candle, DAY_MS, MIN_MS

HOUR_MS = 60 * MIN_MS
# UTC session windows (doc 10 §2.5): asia 00-07, london 07-13, newyork 13-21, dead 21-24
SESSIONS = (("asia", 0, 7), ("london", 7, 13), ("newyork", 13, 21), ("dead", 21, 24))
FIRST_HOURS_MIN = 120


def day_start(ts: int) -> int:
    """00:00 UTC of the UTC day containing `ts` (an instant, not a close ts)."""
    return (ts // DAY_MS) * DAY_MS


def session_of(ts: int) -> tuple[str, int]:
    """(session label, minutes into the session) for an instant `ts`."""
    ms_in_day = ts - day_start(ts)
    hour = ms_in_day / HOUR_MS
    for name, a, b in SESSIONS:
        if a <= hour < b:
            return name, int((ms_in_day - a * HOUR_MS) // MIN_MS)
    return "dead", 0


def session_bounds(day_start_ms: int, name: str) -> tuple[int, int]:
    for n, a, b in SESSIONS:
        if n == name:
            return day_start_ms + a * HOUR_MS, day_start_ms + b * HOUR_MS
    raise KeyError(name)


def candles_between(c15: list[Candle], start_ms: int, end_ms: int) -> list[Candle]:
    """15m candles whose whole (open, close] interval lies inside [start, end]."""
    return [c for c in c15 if c.ts - 15 * MIN_MS + 1 >= start_ms and c.ts <= end_ms]


def session_high_low(c15: list[Candle], day_start_ms: int, name: str, until_ms: Optional[int] = None) -> Optional[dict]:
    a, b = session_bounds(day_start_ms, name)
    if until_ms is not None:
        b = min(b, until_ms)
    cs = candles_between(c15, a, b)
    if not cs:
        return None
    return {"high": max(c.h for c in cs), "low": min(c.l for c in cs), "open": cs[0].o, "n": len(cs),
            "start": a, "end": b}


def asia_range(c15: list[Candle], day_start_ms: int) -> Optional[dict]:
    """High/low of 00:00–07:00 UTC (frozen at 07:00). Requires the full 28 candles."""
    r = session_high_low(c15, day_start_ms, "asia")
    if r is None or r["n"] < 28:
        return None
    a, b = session_bounds(day_start_ms, "asia")
    cs = candles_between(c15, a, b)
    r["wicks_outside"] = 0     # filled by callers with ATR: wicks beyond the range by > 0.1 ATR
    r["candles"] = cs
    return r


def weekday(ts: int) -> int:
    """0 = Monday … 6 = Sunday (UTC)."""
    return _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).weekday()


def week_start(ts: int) -> int:
    """Monday 00:00 UTC of the ISO week containing `ts`."""
    d = day_start(ts)
    return d - weekday(ts) * DAY_MS


def utc_hour(ts: int) -> float:
    return (ts - day_start(ts)) / HOUR_MS
