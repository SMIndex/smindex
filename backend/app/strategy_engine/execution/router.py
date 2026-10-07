"""ExecutionRouter (doc 00 §6). Routes an accepted OrderIntent to an adapter.

This session has exactly two adapters and imports NO venue client anywhere:
  - 'paper'  → PaperExecutor (simulated maker fills; the only path that acts)
  - 'null'   → logs and DROPS (used when a venue has no adapter)
There is deliberately no HL/Perpl execution adapter, so nothing can place a real
order — proven by the Part-F safety test (zero venue calls, zero real orders).
Venue is carried on every intent for Phase 3.
"""
from __future__ import annotations

import logging

from ..intents import OrderIntent
from .paper import PaperExecutor, RestingOrder

logger = logging.getLogger("strategy_engine.execution")

# Adapters that actually exist this session. Any other venue → null (drop).
PAPER = "paper"
NULL = "null"


class NullAdapter:
    """Logs and drops. No order is placed."""
    name = NULL

    def submit(self, intent: OrderIntent) -> None:
        logger.info("null adapter: dropped intent %s %s %s (no execution adapter for venue '%s')",
                    intent.strategy_id, intent.coin, intent.kind, intent.venue)
        return None


class PaperAdapter:
    name = PAPER

    def __init__(self, executor: PaperExecutor) -> None:
        self.executor = executor

    def submit(self, intent: OrderIntent, size: float, market_px: float, tick: float,
               position_side: str, order_id: str, now_ms: int) -> RestingOrder:
        side = "buy" if ((intent.side == "long") == (intent.kind == "entry")) else "sell"
        # entry long → buy; target/stop for a long → sell (reduce); mirror for short.
        if intent.kind in ("target", "stop"):
            side = "sell" if position_side == "long" else "buy"
        elif intent.kind == "entry":
            side = "buy" if intent.side == "long" else "sell"
        order = RestingOrder(
            id=order_id, strategy_id=intent.strategy_id, asset=intent.coin, side=side,
            kind=intent.kind, order_type=intent.order_type, px=(intent.px or market_px),
            sz=size, position_side=position_side, reduce_only=intent.reduce_only,
            placed_ms=now_ms, venue=intent.venue,
        )
        return self.executor.place(order, market_px, tick)


class ExecutionRouter:
    def __init__(self, paper_executor: PaperExecutor) -> None:
        self._paper = PaperAdapter(paper_executor)
        self._null = NullAdapter()

    def adapter_for(self, mode: str, venue: str):
        """paper mode → paper adapter; anything else (incl. any 'live' request) →
        null, because no venue execution adapter exists."""
        if mode == "paper":
            return self._paper
        return self._null
