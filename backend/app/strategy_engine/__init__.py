"""Strategy engine package (doc 00 §2, Phase 1 of doc 00 §9).

Self-contained. The ONLY public interface strategies import is:
    - events.py   (MarketEvent union)
    - intents.py  (OrderIntent)
    - base.py     (Strategy interface)
    - engine.py   (StrategyEngine.on_event)

Nothing outside this package imports strategy internals; strategies import ONLY
these four modules. No strategy logic (s01–s06), no order placement, and no
execution adapter exist in Phase 1 — those are Phase 2/3 and gated on the
owner decisions recorded in STRATEGIES_PHASE1_REPORT.md.
"""
