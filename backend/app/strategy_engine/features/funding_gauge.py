"""Funding gauge (doc 02 §4 + §13 prompt 1) — GAUGE ONLY, never a trade.

Pure compute core (unit-tested); the scheduler feeds it HL predicted funding, the
trailing-30-day 8h-equivalent distribution, and the current Binance rate, then
writes `strat_gauge` and emits a Telegram-outbox alert on crowding-level change.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

# doc 02 §4 thresholds (verbatim). z is SYMMETRIC (owner rule change 2026-09-04):
# |funding_z| >= 2 is EXTREME on either side — the sign gives the crowd side
# (positive = crowd long, negative = crowd short); the one-sided rule read
# BTC at z −3.75 as NORMAL, which was a doc oversight, not intent.
Z_EXTREME = 2.0
Z_ELEVATED = 1.0
BINANCE_EXTREME_POS = 0.0005
BINANCE_EXTREME_NEG = -0.0003
BIAS_WEIGHT = 0.25          # gauge tilt weight in bias (doc 00 §4 / doc 02 §13)


def zscore(value: Optional[float], history) -> Optional[float]:
    if value is None:
        return None
    h = np.asarray(history, dtype=float)
    h = h[np.isfinite(h)]
    if h.size < 2:
        return None
    sd = float(h.std(ddof=0))
    if sd < 1e-12:
        return None
    return (value - float(h.mean())) / sd


def compute_gauge(
    hl_predicted_funding: Optional[float],
    hl_8h_equiv_history_30d,
    binance_rate: Optional[float],
) -> dict:
    """Returns the gauge dict (doc 02 §4). crowding_level is 'insufficient_data'
    when neither a z-score nor a Binance rate is available — never fabricated."""
    hl_8h = hl_predicted_funding * 8.0 if hl_predicted_funding is not None else None
    fz = zscore(hl_8h, hl_8h_equiv_history_30d)

    level = "insufficient_data"
    if fz is not None or binance_rate is not None:
        b_extreme = binance_rate is not None and (binance_rate >= BINANCE_EXTREME_POS or binance_rate <= BINANCE_EXTREME_NEG)
        if (fz is not None and abs(fz) >= Z_EXTREME) or b_extreme:
            level = "EXTREME"
        elif fz is not None and abs(fz) >= Z_ELEVATED:
            level = "ELEVATED"
        else:
            level = "NORMAL"

    tilt = None
    blocked_direction = None
    if fz is not None and hl_predicted_funding is not None:
        s = math.copysign(1.0, hl_predicted_funding) if hl_predicted_funding != 0 else 0.0
        tilt = -s * min(abs(fz) / 3.0, 1.0)          # contrarian (doc 02 §4)
    if level == "EXTREME" and hl_predicted_funding is not None:
        # crowd side pays funding: positive funding => crowd long => block long
        blocked_direction = "long" if hl_predicted_funding > 0 else "short"

    return {
        "hl_funding_8h_equiv": hl_8h,
        "funding_z": fz,
        "crowding_level": level,
        "tilt": tilt,
        "blocked_direction": blocked_direction,
    }
