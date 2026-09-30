"""Strategy interface (doc 00 §2: strategies/base.py).

A Strategy reacts to market events and MAY return OrderIntents. The three
handlers mirror doc 00's `on_candle`, `on_tick`, `on_fill`. All default to
no-op so a Phase-1 skeleton strategy (none exist yet) is trivially a pass-through.

Strategies import ONLY events.py, intents.py and this module. No strategy is
implemented in Phase 1 — s01–s06 are Phase 2/3.
"""
from __future__ import annotations

from typing import Iterable

from .events import CandleClosed, Fill, MarketEvent
from .intents import OrderIntent


class Strategy:
    """Base strategy. Subclasses set `id` and override the handlers they use."""

    #: stable strategy id, e.g. 's01_liq_sweep' (matches strat_strategy_state.strategy_id)
    id: str = "base"

    def on_candle(self, ev: CandleClosed) -> Iterable[OrderIntent]:  # noqa: ARG002
        """Called on every closed candle for the strategy's timeframe(s)."""
        return ()

    def on_tick(self, ev: MarketEvent) -> Iterable[OrderIntent]:  # noqa: ARG002
        """Called on sub-candle events (Trade, BookSnapshot, AssetCtx, Liquidation,
        CohortEvent, FundingGaugeChange). Docs require 1-second evaluation on the
        fill/trade stream — this is the hot path (doc 00 §9 / prompt Part 1.2)."""
        return ()

    def on_fill(self, ev: Fill) -> None:  # noqa: ARG002
        """Called when one of this strategy's orders fills. Returns nothing
        (fills mutate the strategy's internal position state, not new intents)."""
        return None
