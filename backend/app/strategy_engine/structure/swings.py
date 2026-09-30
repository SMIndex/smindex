from __future__ import annotations

from dataclasses import dataclass

from .candles import Candle, SWING_K


@dataclass(frozen=True)
class Swing:
    ts: int            # ts of the swing candle (close time)
    price: float
    kind: str          # 'high' | 'low'
    tf: str
    confirmed_at: int  # close time of the k-th following candle
    index: int         # position in the candle list

    def as_dict(self) -> dict:
        return {"ts": self.ts, "price": self.price, "kind": self.kind, "tf": self.tf, "confirmed_at": self.confirmed_at}


def swings(candles: list[Candle], tf: str, k: int | None = None) -> list[Swing]:
    """Swing highs/lows with k candles each side (k = 2 on 15m/1h/4h, 3 on daily — spec v1.1),
    confirmed on the k-th following close. Strictly greater than the left
    neighbours, >= the right ones (so equal highs both register)."""
    k = SWING_K.get(tf, 3) if k is None else k
    out: list[Swing] = []
    n = len(candles)
    for i in range(k, n - k):
        c = candles[i]
        left = candles[i - k:i]
        right = candles[i + 1:i + k + 1]
        if all(c.h > x.h for x in left) and all(c.h >= x.h for x in right):
            out.append(Swing(c.ts, c.h, "high", tf, candles[i + k].ts, i))
        if all(c.l < x.l for x in left) and all(c.l <= x.l for x in right):
            out.append(Swing(c.ts, c.l, "low", tf, candles[i + k].ts, i))
    return out


def confirmed_before(sw: list[Swing], ts: int) -> list[Swing]:
    return [s for s in sw if s.confirmed_at <= ts]


def highs(sw: list[Swing]) -> list[Swing]:
    return [s for s in sw if s.kind == "high"]


def lows(sw: list[Swing]) -> list[Swing]:
    return [s for s in sw if s.kind == "low"]
