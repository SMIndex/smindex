"""StrategyEngine — the single public entry point (doc 00 §2).

`on_event(MarketEvent) -> list[OrderIntent]` dispatches one event to every
registered strategy and collects the intents they return. It is pure: it holds
no DB handle, places no orders, and has no side effects beyond calling the
strategies. Wiring an intent to an order (risk engine + gate ladder + execution
adapter) is Phase 2/3 and lives OUTSIDE this package.

Phase 1: no strategy is registered, so `on_event` always returns []. The class
exists so the contract, dispatch, and isolation boundary are fixed and testable.
"""
from __future__ import annotations

import logging

from .base import Strategy
from .events import CandleClosed, Fill, MarketEvent
from .intents import OrderIntent

logger = logging.getLogger("strategy_engine.engine")


class StrategyEngine:
    def __init__(self) -> None:
        self._strategies: list[Strategy] = []

    def register(self, strategy: Strategy) -> None:
        if any(s.id == strategy.id for s in self._strategies):
            raise ValueError(f"strategy id already registered: {strategy.id}")
        self._strategies.append(strategy)
        logger.info("registered strategy %s", strategy.id)

    @property
    def strategy_ids(self) -> list[str]:
        return [s.id for s in self._strategies]

    def on_event(self, event: MarketEvent) -> list[OrderIntent]:
        """Dispatch `event` to the right handler on every strategy; return the
        concatenated OrderIntents. A crash in one strategy is logged and isolated
        (doc 00 §9 / prompt Part 1.2: a strategy fault must never take the
        terminal — or the other strategies — down)."""
        intents: list[OrderIntent] = []
        for s in self._strategies:
            try:
                if isinstance(event, CandleClosed):
                    intents.extend(s.on_candle(event))
                elif isinstance(event, Fill):
                    s.on_fill(event)
                else:
                    intents.extend(s.on_tick(event))
            except Exception:  # noqa: BLE001 — isolation is the whole point
                logger.exception("strategy %s raised on %s; skipped", s.id, type(event).__name__)
        return intents
