"""Session windows (doc 05 §13 prompt 1), DST-aware via stdlib zoneinfo.

Windows (UTC):
  EU          : opening range 07:00–07:30, trade window ends 10:00
  US          : opening range starts 13:30 (US daylight) / 14:30 (US standard),
                30-min range, trade window ends 3.5h after the range start
  Monday Asia : opening range 00:00–00:30 (Mondays), trade window ends 03:00

`current_session(dt)` returns (name, phase) with phase in
{'opening_range','trade_window'} or (None, None). `opening_range_from_candles`
is the pure core of `opening_range(coin, session)` (the coin wrapper reads
strat_candles in the scheduler). DST-unit-tested for March/November.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")
_UTC = _dt.timezone.utc


def _as_utc(dt) -> _dt.datetime:
    if isinstance(dt, (int, float)):            # epoch ms
        return _dt.datetime.fromtimestamp(dt / 1000.0, tz=_UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def us_in_dst(dt) -> bool:
    """True when America/New_York observes daylight time at `dt` (offset -4h)."""
    u = _as_utc(dt)
    return u.astimezone(_NY).dst() != _dt.timedelta(0)


def _mins(dt: _dt.datetime) -> int:
    return dt.hour * 60 + dt.minute


def session_windows(dt) -> dict:
    """UTC minute-of-day windows for the calendar day of `dt`:
    {session: {'or_start','or_end','window_end'}} (Monday-Asia only on Mondays)."""
    u = _as_utc(dt)
    is_monday = u.weekday() == 0
    us_or_start = 13 * 60 + 30 if us_in_dst(u) else 14 * 60 + 30
    win = {
        "EU": {"or_start": 7 * 60, "or_end": 7 * 60 + 30, "window_end": 10 * 60},
        "US": {"or_start": us_or_start, "or_end": us_or_start + 30, "window_end": us_or_start + 210},  # +3.5h
    }
    if is_monday:
        win["ASIA_MON"] = {"or_start": 0, "or_end": 30, "window_end": 3 * 60}
    return win


def current_session(dt) -> tuple[Optional[str], Optional[str]]:
    """(session, phase) if `dt` is inside a session, else (None, None)."""
    u = _as_utc(dt)
    m = _mins(u)
    for name, w in session_windows(u).items():
        if w["or_start"] <= m < w["or_end"]:
            return name, "opening_range"
        if w["or_end"] <= m < w["window_end"]:
            return name, "trade_window"
    return None, None


def opening_range_from_candles(candles: list[dict], or_start_min: int, or_end_min: int) -> Optional[dict]:
    """High/low across the 15m candles whose CLOSE falls in [or_start, or_end)
    minutes-of-day UTC. `candles` items: {'ts': epoch_ms, 'h':, 'l':}. Returns
    {'or_high','or_low','n'} or None when no candle covers the range."""
    highs, lows = [], []
    for c in candles:
        m = _mins(_as_utc(c["ts"]))
        if or_start_min < m <= or_end_min:      # close inside the 30-min range
            highs.append(c["h"])
            lows.append(c["l"])
    if not highs:
        return None
    return {"or_high": max(highs), "or_low": min(lows), "n": len(highs)}
