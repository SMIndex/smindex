from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .candles import Candle
from .swings import Swing


@dataclass
class Range:
    tf: str
    high: float
    low: float
    high_ts: Optional[int] = None
    low_ts: Optional[int] = None

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def width(self) -> float:
        return self.high - self.low

    def pct(self, price: float) -> float:
        return (price - self.low) / self.width if self.width > 0 else 0.5

    def position(self, price: float) -> str:
        return "premium" if self.pct(price) > 0.5 else "discount"

    def as_dict(self, price: float) -> dict:
        return {"high": self.high, "low": self.low, "mid": self.mid, "position": self.position(price),
                "pct": round(self.pct(price), 4)}


def current_range(candles: list[Candle], sw: list[Swing], tf: str, price: float, now_ms: int) -> Optional[Range]:
    """Range from the last confirmed swing high ABOVE price to the last confirmed
    swing low BELOW price (the swing pair containing price — doc 10 §2.6).
    Falls back to the extreme of the last 20 candles on the side with no swing."""
    conf = [s for s in sw if s.confirmed_at <= now_ms]
    hi = next((s for s in reversed(conf) if s.kind == "high" and s.price > price), None)
    lo = next((s for s in reversed(conf) if s.kind == "low" and s.price < price), None)
    if not candles:
        return None
    tail = candles[-20:]
    h = hi.price if hi else max(c.h for c in tail)
    l = lo.price if lo else min(c.l for c in tail)
    if h <= l:
        h, l = max(c.h for c in tail), min(c.l for c in tail)
        if h <= l:
            return None
    return Range(tf, h, l, hi.ts if hi else None, lo.ts if lo else None)
