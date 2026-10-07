from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from app.strategy_engine.features import indicators as ind

MIN_MS = 60_000
TF_MS = {"15m": 15 * MIN_MS, "1h": 60 * MIN_MS, "4h": 240 * MIN_MS, "1d": 1440 * MIN_MS}
DAY_MS = TF_MS["1d"]
SWING_K = {"15m": 2, "1h": 2, "4h": 2, "1d": 3}     # spec v1.1 (D-64): k=2 on 15m/1h/4h, 3 on daily


@dataclass(frozen=True)
class Candle:
    ts: int          # close time, epoch ms
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def range(self) -> float:
        return self.h - self.l

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def body_ratio(self) -> float:
        r = self.range
        return self.body / r if r > 0 else 0.0

    @property
    def up(self) -> bool:
        return self.c > self.o

    @property
    def down(self) -> bool:
        return self.c < self.o


def to_candles(rows: Iterable[dict]) -> list[Candle]:
    out = [Candle(int(r["ts"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r.get("v") or 0.0))
           for r in rows]
    out.sort(key=lambda x: x.ts)
    return out


def closed(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Only candles whose close time has passed (ts <= now)."""
    return [c for c in candles if c.ts <= now_ms]


def open_ts(c: Candle, tf: str) -> int:
    return c.ts - TF_MS[tf] + 1


def atr_series(candles: list[Candle], n: int = 14) -> np.ndarray:
    if not candles:
        return np.array([])
    return ind.atr([c.h for c in candles], [c.l for c in candles], [c.c for c in candles], n)


def atr_value(candles: list[Candle], n: int = 14) -> Optional[float]:
    a = atr_series(candles, n)
    if a.size == 0 or not np.isfinite(a[-1]):
        return None
    return float(a[-1])


def utc_day_index(ts: int) -> int:
    """UTC day containing the instant just before `ts` (close-time semantics)."""
    return (ts - 1) // DAY_MS


def aggregate_daily(c4h: list[Candle]) -> list[Candle]:
    """Daily candles derived from 4h candles grouped per UTC day (no daily tf is
    stored — DECISIONS D-03). A day is emitted only when all six 4h candles
    exist so the open/close are the true day open/close."""
    groups: dict[int, list[Candle]] = {}
    for c in c4h:
        groups.setdefault(utc_day_index(c.ts), []).append(c)
    out = []
    for day in sorted(groups):
        g = sorted(groups[day], key=lambda x: x.ts)
        if len(g) < 6:
            continue
        # ts = close time of the day's last 4h candle (same close-time semantics as stored tfs)
        out.append(Candle(g[-1].ts, g[0].o, max(x.h for x in g), min(x.l for x in g), g[-1].c, sum(x.v for x in g)))
    return out


def as_dicts(candles: list[Candle]) -> list[dict]:
    return [{"ts": c.ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v} for c in candles]
