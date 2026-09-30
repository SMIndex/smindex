"""Timing layer (doc 06 §8 + §15 prompt 1) — used by strategies s01–s05.

Pure combiners over candle arrays (unit-tested on synthetic series). The
coin-keyed wrappers `hull_dir(coin, tf, n)` / `fisher_turn(coin, tf, n)` /
`timing_score(coin, direction)` are thin adapters that read `strat_candles`;
they live in the scheduler (Part 3.9) and delegate to these pure functions.

direction encoding: +1 long, -1 short, 0 none (matches the docs).
"""
from __future__ import annotations

import numpy as np

from . import indicators


def hull_direction(close, n: int) -> int:
    """+1 when hull_ma(n) slope > 0 for the last 2 closed candles, -1 when < 0
    for 2 candles, else 0 (doc 06 §8/§15)."""
    hma = indicators.hull_ma(close, n)
    sl = indicators.slope(hma)
    if sl.size < 2 or not (np.isfinite(sl[-1]) and np.isfinite(sl[-2])):
        return 0
    if sl[-1] > 0 and sl[-2] > 0:
        return 1
    if sl[-1] < 0 and sl[-2] < 0:
        return -1
    return 0


def fisher_turn(high, low, n: int = 9, lookback: int = 6) -> int:
    """+1 when fisher(n) reached <= -1.5 within the last `lookback` closed candles
    AND crosses above its signal on the latest candle; -1 for the mirror; else 0.
    signal = previous-bar fisher (doc 06 §2/§15)."""
    fisher, signal = indicators.fisher_transform(high, low, n)
    finite = np.isfinite(fisher)
    if finite.sum() < 3:
        return 0
    t = fisher.size - 1
    if not (np.isfinite(fisher[t]) and np.isfinite(fisher[t - 1]) and np.isfinite(signal[t]) and np.isfinite(signal[t - 1])):
        return 0
    recent = fisher[max(0, t - lookback + 1): t + 1]
    recent = recent[np.isfinite(recent)]
    cross_up = fisher[t] > signal[t] and fisher[t - 1] <= signal[t - 1]
    cross_dn = fisher[t] < signal[t] and fisher[t - 1] >= signal[t - 1]
    if recent.size and recent.min() <= -1.5 and cross_up:
        return 1
    if recent.size and recent.max() >= 1.5 and cross_dn:
        return -1
    return 0


def timing_score(hull_dir_val: int, fisher_turn_val: int, direction: int) -> float:
    """doc 06 §8/§15: 1.0 both agree; 0.6 one agrees + other 0; 0.2 one agrees +
    other opposite; 0 otherwise. `hull_dir_val` is hull_dir(coin,'1h',21),
    `fisher_turn_val` is fisher_turn(coin,'15m',9)."""
    if direction not in (1, -1):
        return 0.0
    h_agree = hull_dir_val == direction
    f_agree = fisher_turn_val == direction
    h_opp = hull_dir_val == -direction
    f_opp = fisher_turn_val == -direction
    if h_agree and f_agree:
        return 1.0
    if (h_agree and fisher_turn_val == 0) or (f_agree and hull_dir_val == 0):
        return 0.6
    if (h_agree and f_opp) or (f_agree and h_opp):
        return 0.2
    return 0.0
