"""Regime gate (doc 00 §4) — evaluated every closed 15m candle.

Returns TRADE_ALLOWED plus a 0..1 regime score. Blocked if ANY of: a scheduled
macro event within the next 2h or the last 30 min; oracle stale > 10 s; BTC/ETH
spread wider than 2x its 24h median; weekend low-liquidity window
(Sat 00:00 → Sun 12:00 UTC). Regime score = mean of (realized vol vs 24h avg,
OI 24h change magnitude, funding extremity, session quality).

Pure core here (unit-tested); the scheduler supplies the real inputs from
strat_events / strat_oi_1m / strat_book_5s / strat_candles and writes strat_regime.
Any input that is not yet available is reported as a blocker/None — never guessed.
"""
from __future__ import annotations

from typing import Optional

_HOUR_MS = 3_600_000
_MIN_MS = 60_000


def macro_block(now_ms: int, event_ts_list) -> bool:
    """True if a macro event is within the next 2h or the last 30 min (doc 00 §4)."""
    for ev in event_ts_list or []:
        if -30 * _MIN_MS <= (ev - now_ms) <= 2 * _HOUR_MS:
            return True
    return False


def weekend_block(now_ms: int) -> bool:
    """Sat 00:00 → Sun 12:00 UTC (doc 00 §4)."""
    import datetime as _dt
    u = _dt.datetime.fromtimestamp(now_ms / 1000.0, tz=_dt.timezone.utc)
    wd = u.weekday()                     # Mon=0 .. Sat=5, Sun=6
    if wd == 5:
        return True
    if wd == 6 and u.hour < 12:
        return True
    return False


def oracle_stale(now_ms: int, last_oracle_ms: Optional[int], max_age_s: int = 10) -> Optional[bool]:
    """True if the oracle has not updated for > 10 s. None if unknown."""
    if last_oracle_ms is None:
        return None
    return (now_ms - last_oracle_ms) > max_age_s * 1000


def spread_block(spread: Optional[float], median_24h_spread: Optional[float]) -> Optional[bool]:
    """True if spread > 2x its 24h median. None if either input unavailable."""
    if spread is None or median_24h_spread is None or median_24h_spread <= 0:
        return None
    return spread > 2.0 * median_24h_spread


def _clamp01(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    return max(0.0, min(1.0, x))


def evaluate(
    now_ms: int,
    *,
    event_ts_list=None,
    last_oracle_ms: Optional[int] = None,
    spread: Optional[float] = None,
    median_24h_spread: Optional[float] = None,
    session_quality: Optional[float] = None,
    rv_vs_24h_avg: Optional[float] = None,
    oi_24h_change_mag: Optional[float] = None,
    funding_extremity: Optional[float] = None,
) -> dict:
    """Returns {'allowed', 'score', 'blockers'}. `blockers` lists the active
    (or unavailable) gate conditions. score is the mean of the AVAILABLE 0..1
    inputs (None when none are available — honest, not zero)."""
    blockers: list[str] = []
    if macro_block(now_ms, event_ts_list):
        blockers.append("macro_event")
    if weekend_block(now_ms):
        blockers.append("weekend")
    ostale = oracle_stale(now_ms, last_oracle_ms)
    if ostale is True:
        blockers.append("oracle_stale")
    elif ostale is None:
        blockers.append("oracle_unavailable")
    sblock = spread_block(spread, median_24h_spread)
    if sblock is True:
        blockers.append("spread_wide")
    elif sblock is None:
        blockers.append("spread_unavailable")

    hard = {"macro_event", "weekend", "oracle_stale", "spread_wide"}
    allowed = not (hard & set(blockers))

    inputs = [
        _clamp01(rv_vs_24h_avg),
        _clamp01(oi_24h_change_mag),
        _clamp01(funding_extremity),
        _clamp01(session_quality),
    ]
    avail = [x for x in inputs if x is not None]
    score = (sum(avail) / len(avail)) if avail else None
    return {"allowed": allowed, "score": score, "blockers": blockers}
