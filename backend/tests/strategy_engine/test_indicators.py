"""Known-value unit tests for the indicator layer (doc 00 §4, doc 06 §2, doc 04 §13)."""
import math

import numpy as np

from app.strategy_engine.features import indicators as ind


def test_ema_known_values():
    # alpha = 2/(3+1) = 0.5, seeded with the first value
    out = ind.ema([1, 2, 3, 4], 3)
    assert math.isclose(out[0], 1.0)
    assert math.isclose(out[1], 1.5)
    assert math.isclose(out[2], 2.25)
    assert math.isclose(out[3], 3.125)


def test_wma_known_value():
    # weights 1,2,3 -> (1*1 + 2*2 + 3*3)/6 = 14/6
    out = ind.wma([1, 2, 3], 3)
    assert math.isclose(out[-1], 14 / 6, rel_tol=1e-9)


def test_hull_ma_positive_slope_on_uptrend():
    close = list(range(1, 40))          # strictly increasing
    hma = ind.hull_ma(close, 8)
    sl = ind.slope(hma)
    tail = sl[np.isfinite(sl)][-3:]
    assert (tail > 0).all()


def test_atr_wilder_known_value():
    h = [10, 11, 12]
    l = [9, 10, 11]
    c = [9.5, 10.5, 11.5]
    a = ind.atr(h, l, c, n=2)
    # TR = [1, 1.5, 1.5]; seed = mean(1,1.5)=1.25 at idx1; idx2 = (1.25*1 + 1.5)/2 = 1.375
    assert math.isclose(a[1], 1.25)
    assert math.isclose(a[2], 1.375)


def test_bbw_zero_on_flat_series():
    out = ind.bollinger_band_width([2, 2, 2, 2, 2], n=3, k=2)
    assert math.isclose(out[-1], 0.0, abs_tol=1e-12)


def test_percentile_rank_bounds():
    assert math.isclose(ind.percentile_rank([1, 2, 3, 4, 5]), 100.0)      # latest is max
    assert math.isclose(ind.percentile_rank([5, 4, 3, 2, 1]), 20.0)       # latest is min (1/5)


def test_realized_vol_nan_when_short():
    assert math.isnan(ind.realized_vol([100, 101, 102], window=60))


def test_realized_vol_positive_on_noise():
    rng = np.random.default_rng(0)
    prices = 100 * np.cumprod(1 + rng.normal(0, 0.001, 200))
    rv = ind.realized_vol(prices, window=60)
    assert rv > 0 and math.isfinite(rv)


def test_fisher_reaches_extreme_and_signal_is_prev_bar():
    # V-shape: sharp decline then recovery -> Fisher should hit a deep negative
    down = list(np.linspace(100, 80, 20))
    up = list(np.linspace(80, 100, 20))
    price = down + up
    high = [p + 0.5 for p in price]
    low = [p - 0.5 for p in price]
    fisher, signal = ind.fisher_transform(high, low, n=9)
    fin = fisher[np.isfinite(fisher)]
    assert fin.min() <= -1.5                      # reached the extreme
    # signal[t] must equal fisher[t-1] wherever both are finite
    for t in range(1, len(fisher)):
        if np.isfinite(signal[t]) and np.isfinite(fisher[t - 1]):
            assert math.isclose(signal[t], fisher[t - 1], rel_tol=1e-9, abs_tol=1e-9)


def test_detect_compression_none_when_no_data():
    assert ind.detect_compression([1, 2], [0, 1], [1, 1]) is None


def test_detect_compression_finds_quiet_tail():
    rng = np.random.default_rng(1)
    # 200 volatile candles, then 20 very quiet ones (compression)
    loud = 100 + np.cumsum(rng.normal(0, 1.5, 200))
    quiet = np.full(20, loud[-1]) + rng.normal(0, 0.02, 20)
    close = np.concatenate([loud, quiet])
    high = close + 0.05
    low = close - 0.05
    box = ind.detect_compression(high, low, close)
    assert box is not None
    assert box["n_candles"] >= 8
    assert box["box_high"] >= box["box_low"]
