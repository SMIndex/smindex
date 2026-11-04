"""Unusual Timing (UT): wallets and clusters whose entries repeatedly precede
big moves, measured against each coin's own base rate. Phase 1 is backend,
CLI and admin Telegram only (E:/smindex-ops/unusual-timing, 2026-11-04).

Two processes share this package:

  * the API process reads Hyperliquid through the shared client at
    BACKGROUND priority (candles, ledger updates, the daily universe
    snapshot) under UT_DAILY_WEIGHT_CAP, and delivers the admin alerts the
    trades feed queues (services/ut/jobs.py, services/ut/live.py);
  * the worker process (scripts/ut_worker.py, service smindex-ut) does the
    CPU work on the stored data: events, coin stats, entries, scores,
    clusters, drift, validation. It never calls Hyperliquid, so the API's
    event loop is not stalled by the arithmetic.

Every public sentence uses "unusual timing" and numbers; no verdicts.
"""
