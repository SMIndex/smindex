"""Copy Trading v1 service package (paper-only).

Modules:
  - audit            : append-only audit_logs + risk_events writers/readers
  - trader_profiles  : trader_profiles + trader_stats_daily upsert/read (discovery)
  - watchlist        : watchlists CRUD (watch != copy)
  - subscriptions    : copy_subscriptions CRUD (forced mode='paper'; live disabled)

NONE of these place real orders. They never import perplTrading, auto_copy, or
pending_copies, and they use only the v1 tables in app/db/copy_models.py.
"""
