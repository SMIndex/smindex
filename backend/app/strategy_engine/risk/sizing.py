"""Position sizing (doc 00 §5) — pure.

Risk 1.5% of equity per trade; size = risk_dollars / stop_distance. Leverage is
an OUTPUT, capped at max_leverage (default 3x notional/equity): if the risk-based
size would need more than the cap, REDUCE size — never widen the stop.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sized:
    size: float            # base units (coin)
    leverage: float        # notional / equity (<= max_leverage)
    notional: float
    risk_dollars: float
    capped: bool           # True when the leverage cap reduced the size


def size_for(entry: float, stop: float, equity: float,
             risk_pct: float = 1.5, max_leverage: float = 3.0) -> Sized:
    stop_distance = abs(entry - stop)
    risk_dollars = equity * (risk_pct / 100.0)
    if stop_distance <= 0 or entry <= 0 or equity <= 0:
        return Sized(0.0, 0.0, 0.0, risk_dollars, False)
    size = risk_dollars / stop_distance
    notional = size * entry
    max_notional = equity * max_leverage
    capped = False
    if notional > max_notional:
        size = max_notional / entry          # reduce size, never widen the stop
        notional = size * entry
        capped = True
    leverage = notional / equity if equity > 0 else 0.0
    return Sized(size=size, leverage=leverage, notional=notional,
                 risk_dollars=risk_dollars, capped=capped)
