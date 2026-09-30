"""Indicators (doc 00 §4, doc 06 §2, doc 04 §13, doc 05 §13) — EXACT formulas.

Pure functions over numpy arrays; no DB, no I/O. Every formula is verbatim from
the docs. Series functions return a float array the same length as the input
with `np.nan` during warm-up. Scalar helpers act on the latest value.

numpy is the only added dependency (declared in requirements.txt, pinned 1.26.4;
Decisions §e). doc 00 minimises deps — no pandas.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

_EPS = 1e-12


def _asarray(x) -> np.ndarray:
    return np.asarray(x, dtype=float)


def ema(series, n: int) -> np.ndarray:
    """Standard EMA, alpha = 2/(n+1). Seeded with the first value; NaN before it."""
    s = _asarray(series)
    out = np.full(s.shape, np.nan)
    if s.size == 0:
        return out
    alpha = 2.0 / (n + 1.0)
    acc = s[0]
    out[0] = acc
    for i in range(1, s.size):
        acc = alpha * s[i] + (1 - alpha) * acc
        out[i] = acc
    return out


def wma(series, n: int) -> np.ndarray:
    """Weighted MA with linear weights 1..n (n newest-weighted)."""
    s = _asarray(series)
    out = np.full(s.shape, np.nan)
    if n <= 0 or s.size < n:
        return out
    w = np.arange(1, n + 1, dtype=float)
    wsum = w.sum()
    for i in range(n - 1, s.size):
        out[i] = float(np.dot(s[i - n + 1: i + 1], w) / wsum)
    return out


def hull_ma(series, n: int) -> np.ndarray:
    """Hull MA: HMA = WMA(2*WMA(price, n/2) - WMA(price, n), sqrt(n)) (doc 06 §2)."""
    s = _asarray(series)
    half = max(1, int(round(n / 2)))
    sq = max(1, int(round(np.sqrt(n))))
    wma_half = wma(s, half)
    wma_full = wma(s, n)
    raw = 2.0 * wma_half - wma_full          # NaN propagates during warm-up
    # WMA needs `sq` non-NaN inputs; feed raw (with its NaNs) — wma handles size
    # but not internal NaNs, so compute only where raw is finite.
    out = np.full(s.shape, np.nan)
    finite_from = np.argmax(np.isfinite(raw)) if np.isfinite(raw).any() else s.size
    if finite_from >= s.size:
        return out
    seg = raw[finite_from:]
    seg_wma = wma(seg, sq)
    out[finite_from:] = seg_wma
    return out


def slope(series) -> np.ndarray:
    """First difference: series[t] - series[t-1]."""
    s = _asarray(series)
    out = np.full(s.shape, np.nan)
    out[1:] = s[1:] - s[:-1]
    return out


def fisher_transform(high, low, n: int = 9) -> tuple[np.ndarray, np.ndarray]:
    """Fisher Transform (doc 06 §2, exact), price = (high+low)/2.

    x   = 2*((price - min_n)/(max_n - min_n) - 0.5), clamped to [-0.999, 0.999]
    x   = 0.33*x + 0.67*x_prev
    fis = 0.5*ln((1+x)/(1-x)); fis = 0.5*fis + 0.5*fis_prev
    signal = fisher[t-1]
    Returns (fisher, signal), NaN before `n` bars.
    """
    h = _asarray(high)
    l = _asarray(low)
    price = (h + l) / 2.0
    size = price.size
    fisher = np.full(size, np.nan)
    signal = np.full(size, np.nan)
    x_prev = 0.0
    fis_prev = 0.0
    for t in range(size):
        if t < n - 1:
            continue
        window = price[t - n + 1: t + 1]
        mn = float(window.min())
        mx = float(window.max())
        rng = mx - mn
        if rng < _EPS:
            rng = _EPS
        raw = 2.0 * ((price[t] - mn) / rng - 0.5)
        raw = max(-0.999, min(0.999, raw))
        x = 0.33 * raw + 0.67 * x_prev
        x = max(-0.999, min(0.999, x))
        fis = 0.5 * np.log((1 + x) / (1 - x))
        fis = 0.5 * fis + 0.5 * fis_prev
        fisher[t] = fis
        signal[t] = fis_prev          # previous bar's fisher (doc: signal = fisher[t-1])
        x_prev = x
        fis_prev = fis
    return fisher, signal


def true_range(high, low, close) -> np.ndarray:
    h = _asarray(high)
    l = _asarray(low)
    c = _asarray(close)
    tr = np.full(h.shape, np.nan)
    if h.size == 0:
        return tr
    tr[0] = h[0] - l[0]
    for t in range(1, h.size):
        tr[t] = max(h[t] - l[t], abs(h[t] - c[t - 1]), abs(l[t] - c[t - 1]))
    return tr


def atr(high, low, close, n: int = 14) -> np.ndarray:
    """ATR(n) via Wilder's smoothing (RMA of true range). NaN before `n` bars."""
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    if tr.size < n:
        return out
    first = float(np.mean(tr[:n]))       # Wilder seed = simple mean of first n TRs
    out[n - 1] = first
    prev = first
    for t in range(n, tr.size):
        prev = (prev * (n - 1) + tr[t]) / n
        out[t] = prev
    return out


def log_returns(series) -> np.ndarray:
    s = _asarray(series)
    out = np.full(s.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.log(s[1:] / s[:-1])
    return out


def realized_vol(close_1m, window: int = 60, periods_per_year: int = 525600) -> float:
    """Annualised realized vol = std of 1-min log returns over the trailing
    `window` minutes * sqrt(minutes/year) (doc 00 §4). Returns the latest scalar;
    NaN if fewer than `window` returns are available. 525600 = minutes/year."""
    r = log_returns(close_1m)
    r = r[np.isfinite(r)]
    if r.size < window:
        return float("nan")
    seg = r[-window:]
    return float(np.std(seg, ddof=0) * np.sqrt(periods_per_year))


def bollinger_band_width(close, n: int = 20, k: float = 2.0) -> np.ndarray:
    """(upper - lower)/middle where middle=SMA(n), bands = middle +/- k*std(n)
    (doc 04 §13). NaN before `n` bars."""
    s = _asarray(close)
    out = np.full(s.shape, np.nan)
    if s.size < n:
        return out
    for t in range(n - 1, s.size):
        win = s[t - n + 1: t + 1]
        mid = float(win.mean())
        sd = float(win.std(ddof=0))
        if abs(mid) < _EPS:
            continue
        out[t] = (2.0 * k * sd) / mid
    return out


def percentile_rank(series, window: int = 200) -> float:
    """Percentile rank (0..100) of the LATEST finite value within the trailing
    `window` finite values (doc 04 §13). NaN if <2 values available."""
    s = _asarray(series)
    s = s[np.isfinite(s)]
    if s.size < 2:
        return float("nan")
    win = s[-window:]
    latest = win[-1]
    return float((win <= latest).sum() / win.size * 100.0)


def realized_vol_24h_percentile(rv_last_24h: float, rv_history_30d) -> float:
    """Percentile rank (0..100) of the last-24h realized vol against the trailing
    30-day distribution (doc 05 §13). The coin-keyed wrapper (which reads the
    stored rv series) lives in the scheduler; this is the pure core."""
    hist = _asarray(rv_history_30d)
    hist = hist[np.isfinite(hist)]
    if not np.isfinite(rv_last_24h) or hist.size < 2:
        return float("nan")
    return float((hist <= rv_last_24h).sum() / hist.size * 100.0)


# ---- warming variants (no waiting on history) ------------------------------
# Same formulas; when fewer than `n` bars exist the available window is used and
# the caller labels the result "warming: M of N bars". Thresholds are unchanged.

def atr_warm(high, low, close, n: int = 14) -> np.ndarray:
    """ATR(n) Wilder series seeded at bar min(n, size): identical to atr() once
    `n` bars exist; before that the seed is the mean of the available TRs."""
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    if tr.size == 0:
        return out
    seed_n = min(n, tr.size)
    first = float(np.mean(tr[:seed_n]))
    out[seed_n - 1] = first
    prev = first
    for t in range(seed_n, tr.size):
        prev = (prev * (n - 1) + tr[t]) / n
        out[t] = prev
    return out


def atr_last(high, low, close, n: int = 14) -> tuple[float, int]:
    """(latest ATR(n) value over the available window, bars available)."""
    a = atr_warm(high, low, close, n)
    have = int(a.size)
    return (float(a[-1]) if have and np.isfinite(a[-1]) else float("nan")), have


def bbw_warm(close, n: int = 20, k: float = 2.0) -> np.ndarray:
    """Bollinger band width with the window shrunk to the available bars during
    warm-up (needs >= 2 bars); identical to bollinger_band_width() from bar n."""
    s = _asarray(close)
    out = np.full(s.shape, np.nan)
    for t in range(1, s.size):
        w = min(n, t + 1)
        win = s[t - w + 1: t + 1]
        mid = float(win.mean())
        sd = float(win.std(ddof=0))
        if abs(mid) < _EPS:
            continue
        out[t] = (2.0 * k * sd) / mid
    return out


def rv_from_closes(closes, window: int, periods_per_year: float) -> tuple[float, int]:
    """Annualised realized vol from log returns of `closes` over the trailing
    `window` returns, shrunk to what exists. Returns (rv, returns_used)."""
    r = log_returns(closes)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return float("nan"), int(r.size)
    seg = r[-window:]
    return float(np.std(seg, ddof=0) * np.sqrt(periods_per_year)), int(seg.size)


def daily_rv_history(closes, bars_per_day: int = 96, periods_per_year: float = 96 * 365) -> np.ndarray:
    """Realized vol per NON-overlapping trailing day (most recent last), from a
    bar series. Used for the 30-day RV percentile until a 1m tape spans 30d."""
    s = _asarray(closes)
    out = []
    end = s.size
    while end - bars_per_day >= 1:
        seg = s[end - bars_per_day - 1: end]
        r = log_returns(seg)
        r = r[np.isfinite(r)]
        if r.size >= 2:
            out.append(float(np.std(r, ddof=0) * np.sqrt(periods_per_year)))
        end -= bars_per_day
    return np.asarray(out[::-1], dtype=float)


def detect_compression(
    high, low, close, ts=None, n_bb: int = 20, k: float = 2.0, atr_n: int = 14,
    lookback: int = 200, warm: bool = False,
) -> Optional[dict]:
    """Volatility-compression box (doc 04 §13 prompt 1). Returns
    {start_ts, box_high, box_low, n_candles, mean_atr} when, at the LATEST candle:
      - bbw percentile rank over `lookback` <= 20, AND
      - ATR(14) percentile rank over `lookback` <= 25, AND
      - at least 8 consecutive candles have ATR rank <= 35,
    else None. ts is optional (indices used when absent)."""
    h = _asarray(high)
    l = _asarray(low)
    c = _asarray(close)
    size = c.size
    if size == 0:
        return None
    bbw = bbw_warm(c, n_bb, k) if warm else bollinger_band_width(c, n_bb, k)
    a = atr_warm(h, l, c, atr_n) if warm else atr(h, l, c, atr_n)
    if not (np.isfinite(bbw[-1]) and np.isfinite(a[-1])):
        return None
    bbw_rank = percentile_rank(bbw, lookback)
    atr_rank = percentile_rank(a, lookback)
    if not (bbw_rank <= 20.0 and atr_rank <= 25.0):
        return None
    # count consecutive latest candles with ATR rank <= 35
    consec = 0
    for t in range(size - 1, -1, -1):
        if not np.isfinite(a[t]):
            break
        rank_t = percentile_rank(a[: t + 1], lookback)
        if np.isfinite(rank_t) and rank_t <= 35.0:
            consec += 1
        else:
            break
    if consec < 8:
        return None
    start_idx = size - consec
    box_hi = float(np.max(h[start_idx:]))
    box_lo = float(np.min(l[start_idx:]))
    mean_atr = float(np.nanmean(a[start_idx:]))
    start_ts = (ts[start_idx] if ts is not None else start_idx)
    return {
        "start_ts": start_ts,
        "box_high": box_hi,
        "box_low": box_lo,
        "n_candles": consec,
        "mean_atr": mean_atr,
    }
