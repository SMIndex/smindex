"""Server-side auto-copy (spec SMINDEX_FEATURES_8-10 Part 2), a NEW execution
path next to live_manual. Named copy_auto to stay clear of the dead v0
`services/auto_copy.py` and `/api/autocopy/*` (still mounted, never executed).
Nothing in app/strategy_engine may import this package (static safety scan)."""
