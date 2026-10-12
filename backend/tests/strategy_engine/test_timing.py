"""Synthetic-series tests for the timing layer (doc 06 §8/§15)."""
import numpy as np

from app.strategy_engine.features import timing


def test_hull_direction_up_and_down():
    up = list(range(1, 40))
    dn = list(range(40, 1, -1))
    assert timing.hull_direction(up, 8) == 1
    assert timing.hull_direction(dn, 8) == -1


def test_hull_direction_zero_on_flat():
    assert timing.hull_direction([5.0] * 30, 8) == 0


def test_timing_score_matrix():
    # both agree -> 1.0
    assert timing.timing_score(1, 1, 1) == 1.0
    # one agrees, other 0 -> 0.6
    assert timing.timing_score(1, 0, 1) == 0.6
    assert timing.timing_score(0, 1, 1) == 0.6
    # one agrees, other opposite -> 0.2
    assert timing.timing_score(1, -1, 1) == 0.2
    assert timing.timing_score(-1, 1, 1) == 0.2
    # both disagree / none -> 0
    assert timing.timing_score(-1, -1, 1) == 0.0
    assert timing.timing_score(0, 0, 1) == 0.0
    # short side mirrors
    assert timing.timing_score(-1, -1, -1) == 1.0


def test_fisher_turn_up_from_extreme():
    # deep V: forces Fisher to a low then an upward cross on the recovery
    down = list(np.linspace(100, 70, 22))
    up = list(np.linspace(70, 100, 12))
    price = np.array(down + up)
    high = price + 0.5
    low = price - 0.5
    # The turn fires just after the V-bottom (extreme still within the 6-candle
    # lookback and Fisher crosses up). Scan every closed candle for the +1.
    seen_up = False
    for end in range(12, len(price) + 1):
        if timing.fisher_turn(high[:end], low[:end], n=9) == 1:
            seen_up = True
            break
    assert seen_up
