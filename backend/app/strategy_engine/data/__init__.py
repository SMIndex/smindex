"""Data-layer feeds (doc 00 §3, prompt Part 2). Market-wide HL websocket +
REST backfills + CEX funding polls, persisting to the strat_ tables. All HL ws
subscriptions are MARKET-WIDE (trades/l2Book/candle/activeAssetCtx) and consume
ZERO user slots — asserted in feeds.py. Nothing here trades."""
