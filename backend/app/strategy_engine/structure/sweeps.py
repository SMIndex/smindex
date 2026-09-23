from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .candles import Candle, utc_day_index

MIN_DEPTH_ATR = 0.1
MAX_DEPTH_ATR = 0.5
MAX_WAIT = 3


@dataclass
class Sweep:
    level: float
    side: str               # 'low' (wick below the level) | 'high'
    wick_index: int
    wick_ts: int
    wick_price: float
    depth_atr: float
    reclaimed: bool
    reclaim_index: Optional[int] = None
    reclaim_ts: Optional[int] = None
    reclaim_quality: float = 0.0
    candles_to_reclaim: int = 0
    too_deep: bool = False

    def as_dict(self) -> dict:
        return {"level": self.level, "side": self.side, "wick_ts": self.wick_ts, "wick_price": self.wick_price,
                "depth_atr": round(self.depth_atr, 3), "reclaimed": self.reclaimed, "reclaim_ts": self.reclaim_ts,
                "reclaim_quality": round(self.reclaim_quality, 3), "candles_to_reclaim": self.candles_to_reclaim,
                "too_deep": self.too_deep}


def detect_sweeps(candles: list[Candle], level: float, atr: float, side: str, min_depth: float = MIN_DEPTH_ATR,
                  max_depth: float = MAX_DEPTH_ATR, max_wait: int = MAX_WAIT, start: int = 0) -> list[Sweep]:
    """Wick beyond `level` by >= min_depth ATR (flagged too_deep above max_depth),
    then within max_wait candles a close back on the original side; the reclaim
    candle is the first such close, reclaim_quality = body / range (doc 10 §2.7).
    A wick candle that itself closes back inside counts as reclaimed in 1 candle."""
    out: list[Sweep] = []
    if atr <= 0:
        return out
    i = max(start, 0)
    n = len(candles)
    while i < n:
        c = candles[i]
        beyond = (level - c.l) if side == "low" else (c.h - level)
        depth = beyond / atr
        if depth < min_depth:
            i += 1
            continue
        sw = Sweep(level, side, i, c.ts, c.l if side == "low" else c.h, depth, False, too_deep=depth > max_depth)
        # the wick extreme may extend over following candles before the reclaim
        j = i
        while j < n and j <= i + max_wait - 1:
            cj = candles[j]
            inside = cj.c > level if side == "low" else cj.c < level
            ext = (level - cj.l) / atr if side == "low" else (cj.h - level) / atr
            if ext > sw.depth_atr:
                sw.depth_atr = ext
                sw.wick_price = cj.l if side == "low" else cj.h
                sw.too_deep = sw.depth_atr > max_depth
            if inside:
                sw.reclaimed = True
                sw.reclaim_index = j
                sw.reclaim_ts = cj.ts
                sw.reclaim_quality = cj.body_ratio
                sw.candles_to_reclaim = j - i + 1
                break
            j += 1
        out.append(sw)
        i = (sw.reclaim_index + 1) if sw.reclaimed else (j + 1)
    return out


def sweep_count_today(sweeps: list[Sweep], now_ms: int) -> int:
    day = utc_day_index(now_ms)
    return sum(1 for s in sweeps if utc_day_index(s.wick_ts) == day)


def latest_reclaimed(sweeps: list[Sweep], last_index: int) -> Optional[Sweep]:
    """The sweep whose reclaim candle is the latest closed candle (index)."""
    for s in reversed(sweeps):
        if s.reclaimed and s.reclaim_index == last_index:
            return s
    return None


@dataclass
class Confirmation:
    sweep: Sweep
    confirmation_used: bool     # True = same-candle sweep+reclaim confirmed by one more close (case b)
    trigger_index: int          # candle on whose close the entry is placed (== last_index)
    wick_candle_index: int      # candle carrying the sweep wick extreme


def wick_candle_index(candles: list[Candle], sw: Sweep) -> int:
    """Index of the candle whose extreme IS the sweep's wick_price (the wick may
    have been deepened by a candle after wick_index — detect_sweeps keeps
    wick_index at the first candle beyond the level)."""
    end = sw.reclaim_index if sw.reclaim_index is not None else sw.wick_index
    for k in range(sw.wick_index, min(end, len(candles) - 1) + 1):
        c = candles[k]
        if (c.l if sw.side == "low" else c.h) == sw.wick_price:
            return k
    return sw.wick_index


def confirmed_reclaim(sweeps: list[Sweep], candles: list[Candle], level: float, side: str,
                      last_index: int) -> Optional[Confirmation]:
    """Spec v1.3 Part 3 (D-89), M1/M5 entry definition. A reclaimed sweep is
    confirmed on the LAST closed candle when either
      (a) the reclaim close is on a later candle than the wick candle and that
          reclaim candle is the last candle (later-candle reclaim), or
      (b) wick and reclaim are the SAME candle (it closed back inside), that candle
          is last-1, and the last candle closes back on the original side of the
          level with its extreme not beyond the sweep wick (low >= wick low for a
          low sweep; high <= wick high for a high sweep).
    Returns None when nothing is confirmed on this candle."""
    for sw in reversed(sweeps):
        if not sw.reclaimed or sw.reclaim_index is None:
            continue
        wk = wick_candle_index(candles, sw)
        same = wk == sw.reclaim_index
        if not same and sw.reclaim_index == last_index:
            return Confirmation(sw, False, last_index, wk)
        if same and sw.reclaim_index == last_index - 1 and last_index < len(candles):
            c = candles[last_index]
            if side == "low":
                ok = c.c > level and c.l >= sw.wick_price
            else:
                ok = c.c < level and c.h <= sw.wick_price
            if ok:
                return Confirmation(sw, True, last_index, wk)
    return None
