"""OrderIntent — a strategy's REQUEST to act (doc 00 §5/§6).

An OrderIntent is intent only: it never places an order. In Phase 1 nothing
produces an OrderIntent (no strategy logic, no execution adapter). When Phase 2/3
add strategies + a paper/live adapter, intents will flow engine → risk engine →
execution, and only the risk engine and a gate ladder may turn an intent into a
real order. The type is defined now so the contract is complete and stable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True, slots=True)
class OrderIntent:
    strategy_id: str
    venue: str                      # 'hl' | 'perpl'
    coin: str
    side: str                       # 'long' | 'short'
    kind: str                       # 'entry' | 'stop' | 'target'
    # doc 00 §5/§6: entries + targets are post-only maker; stops are the one
    # allowed taker (trigger market). No market entries.
    order_type: str                 # 'post_only_limit' | 'trigger_market' | 'reduce_limit'
    sz: float                       # base size (coin units)
    px: Optional[float] = None      # limit / trigger price; None only for market-style stops
    reduce_only: bool = False
    tif: str = "Alo"                # HL add-liquidity-only (post-only) by default
    reason: str = ""                # human-readable one-line reason (doc 00 §8)
    meta: dict = field(default_factory=dict)
