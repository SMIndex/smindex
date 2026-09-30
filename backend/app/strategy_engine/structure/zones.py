from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .candles import Candle
from .displacement import Displacement, detect


@dataclass
class Zone:
    type: str          # 'OB' | 'FVG'
    tf: str
    direction: str     # 'bullish' | 'bearish'
    top: float
    bottom: float
    created_ts: int
    created_index: int
    status: str = "fresh"     # OB: fresh|tested|broken ; FVG: open|half_filled|filled
    grade: float = 0.0        # displacement grade that created it
    disp_high: float = 0.0
    disp_low: float = 0.0

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def live(self) -> bool:
        return self.status not in ("broken", "filled")

    def as_dict(self) -> dict:
        return {"type": self.type, "tf": self.tf, "direction": self.direction, "top": self.top,
                "bottom": self.bottom, "mid": self.mid, "status": self.status, "ts": self.created_ts, "grade": self.grade}


def order_block(candles: list[Candle], disp: Displacement, tf: str) -> Optional[Zone]:
    """Bullish OB = body (open→close) of the last down candle before an up
    displacement; mirror for bearish. Looks back at most 3 candles."""
    j = disp.start_index - 1
    while j >= 0 and j >= disp.start_index - 3:
        c = candles[j]
        if (disp.direction == "up" and c.down) or (disp.direction == "down" and c.up):
            return Zone("OB", tf, "bullish" if disp.direction == "up" else "bearish", max(c.o, c.c), min(c.o, c.c),
                        c.ts, j, "fresh", disp.grade, disp.high, disp.low)
        j -= 1
    return None


def fvg_at(candles: list[Candle], i: int, tf: str, grade_: float = 0.0) -> Optional[Zone]:
    """FVG with candle i as the middle candle: bullish when c1.high < c3.low."""
    if i < 1 or i + 1 >= len(candles):
        return None
    c1, c3 = candles[i - 1], candles[i + 1]
    if c1.h < c3.l:
        return Zone("FVG", tf, "bullish", c3.l, c1.h, c3.ts, i + 1, "open", grade_)
    if c1.l > c3.h:
        return Zone("FVG", tf, "bearish", c1.l, c3.h, c3.ts, i + 1, "open", grade_)
    return None


def update_status(z: Zone, later: list[Candle]) -> Zone:
    """Status from the candles after creation (doc 10 §2.4)."""
    if z.type == "OB":
        for c in later:
            if z.direction == "bullish":
                if c.c < z.bottom:
                    z.status = "broken"
                    return z
                if c.l <= z.top:
                    z.status = "tested"
            else:
                if c.c > z.top:
                    z.status = "broken"
                    return z
                if c.h >= z.bottom:
                    z.status = "tested"
        return z
    for c in later:
        if z.direction == "bullish":
            if c.l <= z.bottom:
                z.status = "filled"
                return z
            if c.l <= z.mid:
                z.status = "half_filled"
        else:
            if c.h >= z.top:
                z.status = "filled"
                return z
            if c.h >= z.mid:
                z.status = "half_filled"
    return z


def zones(candles: list[Candle], atr_series, tf: str, lookback: int = 200) -> list[Zone]:
    """All OB/FVG zones created by displacements in the last `lookback` candles,
    with current status (oldest first)."""
    out: list[Zone] = []
    n = len(candles)
    start = max(2, n - lookback)
    seen: set[tuple] = set()
    for i in range(start, n):
        a = float(atr_series[i]) if i < len(atr_series) and np.isfinite(atr_series[i]) else 0.0
        d = detect(candles, a, i)
        if d is None:
            continue
        ob = order_block(candles, d, tf)
        if ob:
            key = ("OB", ob.created_index, ob.direction)
            if key not in seen:
                seen.add(key)
                update_status(ob, candles[d.end_index + 1:])
                out.append(ob)
        for k in range(d.start_index, d.end_index + 1):
            f = fvg_at(candles, k, tf, d.grade)
            if f and f.direction == ("bullish" if d.direction == "up" else "bearish"):
                key = ("FVG", k, f.direction)
                if key in seen:
                    continue
                seen.add(key)
                f.disp_high, f.disp_low = d.high, d.low
                update_status(f, candles[k + 2:])
                out.append(f)
    return out


def nearest_zone(zs: list[Zone], price: float, above: bool, live_only: bool = True) -> Optional[Zone]:
    cands = []
    for z in zs:
        if live_only and not z.live:
            continue
        if above and z.bottom > price:
            cands.append((z.bottom - price, z))
        elif not above and z.top < price:
            cands.append((price - z.top, z))
    if not cands:
        return None
    return min(cands, key=lambda x: x[0])[1]
