"""Copy Trading v1 models.

These are the NEW, clean tables for the v1 copy-trading refactor (paper-only).
They live in their own module to keep v1 clearly separated from the legacy
copy-v0 tables in `models.py` (Leader / FollowerConfig / LeaderTrade /
CopyTradeExecution / CopyTradeLog / WalletFollow / PendingCopy / CopyPosition).

Design rules (see COPY_TRADING_ARCHITECTURE.md):
  * Watch  ≠ Copy   → `watchlists` vs `copy_subscriptions`
  * Paper  ≠ Live   → `copy_subscriptions.mode` ('paper' | 'live')
  * NO live order execution in v1 — `live_enabled` is forced False and gated by
    the `COPY_LIVE_ENABLED` config flag. No code path here places a real order.
  * All money / price / size / ratio fields use DECIMAL (Numeric), never Float.
  * Status / type / side / mode values are plain strings for now (no DB enums).
  * Idempotency: `leader_trade_events.unique_event_key` and
    `copy_orders.idempotency_key` are UNIQUE to make event/copy creation safe to
    retry exactly once.

NOTE: Phase A only DEFINES these tables (created via SQLAlchemy `create_all`).
No service writes to them yet — that lands in later phases.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Column, Integer, String, Boolean, DateTime, Date, Text, JSON,
    Numeric, ForeignKey, Index, UniqueConstraint, text,
)

from app.db.database import Base

# --- DECIMAL precision conventions (no Float anywhere) ---
USD = Numeric(20, 8)      # money amounts (allocation, pnl, margin, volume)
PRICE = Numeric(30, 10)   # market prices (BTC large, others small)
SIZE = Numeric(30, 10)    # position / order size in base units
RATIO = Numeric(12, 4)    # percentages & multipliers (roi, win_rate, leverage, sl/tp %)


class TraderProfile(Base):
    """Trader identity for discovery & profiles. Replaces the identity half of
    the legacy `leaders` table. One row per trader wallet."""
    __tablename__ = "trader_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # migration v2: a trader identity is (exchange, wallet) — the same wallet
    # can exist independently on Perpl and Hyperliquid.
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")
    wallet_address = Column(String(42), nullable=False, index=True)
    display_name = Column(String(100), nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    source = Column(String(20), nullable=False, default="leaderboard")  # leaderboard | manual
    bio = Column(Text, nullable=True)
    # Admin moderation (migration v2): hidden profiles are excluded from every
    # public list/profile surface; ingest must never resurrect them.
    is_hidden = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    hidden_at = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    # migration v5: UTC time of the trader's most recent DETECTED fill/trade
    # (ws/poll detection paths; forward-only). Nullable — null sorts last.
    last_fill_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("exchange", "wallet_address", name="uq_trader_profile_exch_wallet"),
    )


class TraderStatsDaily(Base):
    """One snapshot row per trader per UTC day. Replaces the stats half of the
    legacy `leaders` table (and overlaps `leaderboard_snapshots`)."""
    __tablename__ = "trader_stats_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trader_wallet = Column(String(42), nullable=False, index=True)
    stat_date = Column(Date, nullable=False)  # UTC calendar day
    pnl = Column(USD, nullable=True)
    roi = Column(RATIO, nullable=True)
    volume = Column(USD, nullable=True)
    win_rate = Column(RATIO, nullable=True)
    trades = Column(Integer, nullable=False, default=0)
    max_drawdown = Column(USD, nullable=True)
    rank = Column(Integer, nullable=True)
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trader_wallet", "stat_date", name="uq_trader_stats_day"),
        Index("ix_trader_stats_wallet_date", "trader_wallet", "stat_date"),
    )


class Watchlist(Base):
    """Pure WATCH — read-only interest in a trader. No money, no copy config.
    Replaces the 'watch' half of the legacy `wallet_follows` table."""
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    trader_wallet = Column(String(42), nullable=False)
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")  # migration v2
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("follower_wallet", "trader_wallet", "exchange", name="uq_watchlist_pair_exch"),
    )


class CopySubscription(Base):
    """A configured COPY relationship + risk settings. Replaces the 'copy config'
    half of legacy `wallet_follows` (+ `follower_configs` + autocopy config).

    `mode` selects paper vs live; `live_enabled` is the per-subscription gate and
    is FORCED False in v1 (also globally gated by settings.COPY_LIVE_ENABLED).
    """
    __tablename__ = "copy_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    trader_wallet = Column(String(42), nullable=False)
    # migration v2: which exchange the copied trader is observed on. Copy
    # EXECUTION remains Perpl-only regardless — this only scopes the signal.
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")
    mode = Column(String(12), nullable=False, default="live_manual")  # paper | live_manual | live_auto
    status = Column(String(10), nullable=False, default="active")     # active | paused | stopped
    sizing_mode = Column(String(12), nullable=False, default="fixed")  # fixed | proportional

    allocation_usd = Column(USD, nullable=False, default=10)
    max_leverage = Column(RATIO, nullable=False, default=5)
    max_margin_per_trade = Column(USD, nullable=True)
    max_daily_loss = Column(USD, nullable=True)
    max_total_loss = Column(USD, nullable=True)          # added in Phase B (risk limit)
    slippage_bps = Column(Integer, nullable=True)        # basis points (discrete), not a fractional ratio
    allowed_markets = Column(JSON, nullable=True)        # list[int] of market_ids; null = all
    copy_new_only = Column(Boolean, nullable=False, default=True)
    sl_pct = Column(RATIO, nullable=True)
    tp_pct = Column(RATIO, nullable=True)
    # migration v2/phase 5: max |cross-venue basis| in bps allowed at copy time
    # (HL-signal subscriptions only; null = no basis gate)
    max_basis_bps = Column(Integer, nullable=True)

    live_enabled = Column(Boolean, nullable=False, default=False)  # FORCED False in v1

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("follower_wallet", "trader_wallet", name="uq_copy_sub_pair"),
        Index("ix_copy_sub_status_mode", "status", "mode"),
    )


class LeaderTradeEvent(Base):
    """Canonical record of a leader action (detected on-chain). Drives the paper
    copy engine. Replaces legacy `leader_trades` and the copy-relevant half of
    `trader_activity`.

    `unique_event_key` makes ingestion idempotent — the detector composes a stable
    key (e.g. "<wallet>:<market>:<event_type>:<block_or_ts>") so the same detected
    event is never inserted twice across restarts/retries.
    """
    __tablename__ = "leader_trade_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")  # migration v2
    trader_wallet = Column(String(42), nullable=False, index=True)
    market_id = Column(Integer, nullable=False)
    # 20 chars: HL native symbols like "xyz:BRENTOIL" exceed the old 10
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)        # long | short
    event_type = Column(String(12), nullable=False)  # open | close | increase | decrease
    size = Column(SIZE, nullable=True)
    price = Column(PRICE, nullable=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    source = Column(String(20), nullable=False, default="onchain_poll")
    raw = Column(JSON, nullable=True)
    unique_event_key = Column(String(120), unique=True, nullable=False, index=True)

    __table_args__ = (
        Index("ix_leader_event_wallet_ts", "trader_wallet", "detected_at"),
    )


class CopyOrder(Base):
    """Every copy ATTEMPT (one per subscription/leader event, or one per manual
    user confirmation). Tracks REAL live order execution as well as paper sims.
    Replaces legacy `copy_trade_executions` + `copy_trade_logs`.

    `idempotency_key` is UNIQUE so exactly one copy order exists per logical attempt
    (paper: per (sub, event); live_manual: per user confirmation), safe to retry.

    `mode`: paper | live_manual | live_auto.
    `status`: pending_confirmation | submitted | filled | failed | skipped |
              risk_blocked | simulated (paper). The perpl_* / *_at / error_message
              fields are populated for live orders as the real order progresses.
    """
    __tablename__ = "copy_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer, ForeignKey("copy_subscriptions.id"), nullable=True, index=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    trader_wallet = Column(String(42), nullable=False)
    leader_event_id = Column(Integer, ForeignKey("leader_trade_events.id"), nullable=True)
    mode = Column(String(12), nullable=False, default="paper")  # paper | live_manual | live_auto
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    intended_size = Column(SIZE, nullable=True)
    intended_price = Column(PRICE, nullable=True)
    leverage = Column(RATIO, nullable=True)
    allocation_usd = Column(USD, nullable=True)
    action = Column(String(16), nullable=False, default="open")  # open | close | reduce
    # soft link to copy_live_positions.id for close/reduce orders (no FK to avoid cycle)
    live_position_id = Column(Integer, nullable=True, index=True)
    # pending_confirmation | submitted | filled | failed | skipped | risk_blocked | simulated
    status = Column(String(20), nullable=False, default="simulated")
    skip_reason = Column(String(60), nullable=True)
    idempotency_key = Column(String(120), unique=True, nullable=False, index=True)
    # --- order shape (live execution v2, migration v4) ---
    order_type = Column(String(10), nullable=True)   # market | limit (null = legacy market)
    tp_price = Column(PRICE, nullable=True)          # intended take-profit trigger
    sl_price = Column(PRICE, nullable=True)          # intended stop-loss trigger
    tp_order_id = Column(String(80), nullable=True)  # real Perpl trigger order id once placed
    sl_order_id = Column(String(80), nullable=True)
    # --- real Perpl execution tracking (live orders only) ---
    perpl_request_id = Column(String(80), nullable=True)
    perpl_order_id = Column(String(80), nullable=True)
    perpl_fill_id = Column(String(80), nullable=True)
    fill_price = Column(PRICE, nullable=True)
    fill_size = Column(SIZE, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_copy_order_sub_ts", "subscription_id", "created_at"),
    )


class RiskEvent(Base):
    """Audit of every risk-gate decision that blocked or modified a copy.
    Used by the paper engine now; the same gates feed the (future) live engine."""
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer, ForeignKey("copy_subscriptions.id"), nullable=True, index=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    # max_daily_loss | max_margin | slippage | market_not_allowed | leverage_clamped | ...
    event_type = Column(String(40), nullable=False)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AuditLog(Base):
    """Generic, append-only audit trail for user + system copy actions
    (watch / unwatch / subscribe / update / pause / resume / paper_copy_open / ...)."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_wallet = Column(String(42), nullable=True, index=True)
    action = Column(String(40), nullable=False)
    entity_type = Column(String(30), nullable=True)
    entity_id = Column(Integer, nullable=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class CopyPaperPosition(Base):
    """SIMULATED copy position (paper-only) — the v1 positions store.

    Named `copy_paper_positions` (NOT `copy_positions`) because the legacy v0
    `copy_positions` table still exists. There is at most one OPEN row per
    (subscription_id, market_id). Opened/updated/closed by the paper engine; no
    real order is ever placed.
    """
    __tablename__ = "copy_paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer, ForeignKey("copy_subscriptions.id"), nullable=False, index=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    trader_wallet = Column(String(42), nullable=False)
    leader_event_id = Column(Integer, ForeignKey("leader_trade_events.id"), nullable=True)
    mode = Column(String(10), nullable=False, default="paper")
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)          # long | short
    entry_price = Column(PRICE, nullable=False)
    size = Column(SIZE, nullable=False)
    leverage = Column(RATIO, nullable=False)
    allocation_usd = Column(USD, nullable=False)
    status = Column(String(12), nullable=False, default="open")  # open | closed | liquidated
    close_price = Column(PRICE, nullable=True)
    realized_pnl = Column(USD, nullable=True)
    unrealized_pnl = Column(USD, nullable=True, default=0)
    close_reason = Column(String(20), nullable=True)   # leader_close | leader_reduce | liquidation | manual
    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_paper_pos_follower_status", "follower_wallet", "status"),
        Index("ix_paper_pos_sub_market_status", "subscription_id", "market_id", "status"),
    )


class CopyLivePosition(Base):
    """REAL (live_manual) copied position lifecycle. Created when a live copy OPEN
    order fills; updated/closed when the follower confirms a close/reduce (real
    order placed CLIENT-SIDE). The backend never places an order — it only tracks.

    Distinct from `copy_paper_positions` (sandbox sims). `copy_order_id` links to the
    opening order and is the idempotency anchor (one live position per open order).
    Leader close/reduce sets a SUGGESTION (close_suggested) — never an auto order.
    """
    __tablename__ = "copy_live_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    copy_order_id = Column(Integer, ForeignKey("copy_orders.id"), nullable=True, unique=True, index=True)
    subscription_id = Column(Integer, ForeignKey("copy_subscriptions.id"), nullable=True, index=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    trader_wallet = Column(String(42), nullable=False)
    leader_event_id = Column(Integer, ForeignKey("leader_trade_events.id"), nullable=True)
    leader_position_id = Column(String(80), nullable=True)
    follower_market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)            # long | short
    # open | partially_closed | closed | failed | orphaned
    status = Column(String(20), nullable=False, default="open")
    entry_price = Column(PRICE, nullable=True)
    entry_size = Column(SIZE, nullable=True)
    entry_margin = Column(USD, nullable=True)
    entry_leverage = Column(RATIO, nullable=True)
    current_size = Column(SIZE, nullable=True)
    close_price = Column(PRICE, nullable=True)
    realized_pnl = Column(USD, nullable=True)
    # leader-driven close/reduce SUGGESTION (display + confirm; never an auto order)
    # migration v7 (audit A1): TP/SL trigger placement failed after the open
    # fill — the position is UNPROTECTED and the UI must show it loudly.
    triggers_failed = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    close_suggested = Column(Boolean, nullable=False, default=False)
    close_suggestion_type = Column(String(12), nullable=True)   # close | reduce
    suggested_at = Column(DateTime, nullable=True)
    suggested_event_id = Column(Integer, nullable=True)
    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    close_reason = Column(String(30), nullable=True)     # manual | leader_close | leader_reduce | failed
    raw = Column(JSON, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_live_pos_follower_status", "follower_wallet", "status"),
        Index("ix_live_pos_trader_market_status", "trader_wallet", "follower_market_id", "status"),
    )


class WalletTradeInsight(Base):
    """One reconstructed closed round-trip (open->close span) for a tracked
    wallet, rebuilt from `leader_trade_events` with exit legs priced from Perpl
    5-min candles. est_pnl is an ESTIMATE (candle close at each reduce/close,
    gross of fees/rebates). Leverage is NOT recorded historically by the tracker
    (events carry no lv/deposit), so it is intentionally absent here.
    Rebuilt in full by wallet_insights.refresh() — rows are disposable."""
    __tablename__ = "wallet_trade_insights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet = Column(String(42), nullable=False, index=True)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)          # long | short
    open_t = Column(DateTime, nullable=True)           # NULL = opened before tracking started
    close_t = Column(DateTime, nullable=False, index=True)
    hold_sec = Column(Integer, nullable=True)
    max_size = Column(SIZE, nullable=True)
    avg_entry = Column(PRICE, nullable=True)           # last avg entry seen for the span
    notional_usd = Column(USD, nullable=True)          # max_size * avg_entry
    leverage = Column(RATIO, nullable=True)            # from event raw (recorded from 2026-07-31 on)
    est_pnl = Column(USD, nullable=True)               # NULL = no candle data for any leg
    legs = Column(Integer, nullable=False, default=1)  # priced reduce/close legs
    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wti_wallet_close", "wallet", "close_t"),
    )


class MarketMap(Base):
    """migration v2 — cross-exchange market identity.

    Maps an exchange-native symbol (HL coin name) to the Perpl market id it can
    be copied onto. An HL coin with no row here simply is not copyable on Perpl
    (rendered greyed-out, never guessed). `canonical_symbol` is the display
    symbol shared across venues.
    """
    __tablename__ = "market_map"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String(20), nullable=False)
    native_symbol = Column(String(20), nullable=False)
    perpl_market_id = Column(Integer, nullable=True)
    canonical_symbol = Column(String(20), nullable=False)

    __table_args__ = (
        UniqueConstraint("exchange", "native_symbol", name="uq_market_map_exch_native"),
    )

class TraderActivityMetrics(Base):
    """Tier-1 activity/consistency metrics (migration v6) — derived hourly from
    the leaderboard_snapshots history already in DB (ZERO extra venue load).
    NULL field = not derivable for that wallet/exchange (rendered em-dash,
    never faked): Perpl snapshots carry only the cumulative 'all' window, so
    consistency_flags / pnl_trend_7d stay NULL there.

    consistency_flags bitmask (HL): bit set = wallet HAS a row in the LATEST
    ingest batch of that window AND that window's PnL > 0.
      bit0 (1) = day · bit1 (2) = week · bit2 (4) = month · bit3 (8) = allTime
    Absent-from-window means "not provably profitable there", not "losing".
    """
    __tablename__ = "trader_activity_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String(20), nullable=False)
    wallet_address = Column(String(42), nullable=False)
    active_days_7d = Column(Integer, nullable=True)      # days with volume growth, last 7
    active_days_30d = Column(Integer, nullable=True)
    last_active_at = Column(DateTime, nullable=True)     # last snapshot where volume grew
    vol_velocity_24h = Column(USD, nullable=True)        # traded volume over last ~24h
    consistency_flags = Column(Integer, nullable=True)   # bitmask above (HL only)
    pnl_trend_7d = Column(USD, nullable=True)            # LSQ slope of week-PnL, USD/day (HL only)
    history_days = Column(Integer, nullable=True)        # distinct snapshot days available (cap 30)
    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("exchange", "wallet_address", name="uq_activity_exch_wallet"),
    )


class TraderFillStats(Base):
    """Tier-2 fills-based quality stats (migration v6) — HL top-50-per-window
    union, sampled hourly by services/hyperliquid/fill_stats.py under a hard
    request budget. Small samples are surfaced (sample_fills / trades_7d), and
    win_rate_7d is NULL below 10 trades — a percentage from 3 trades is noise.
    """
    __tablename__ = "trader_fill_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42), nullable=False, unique=True)  # HL only
    trades_7d = Column(Integer, nullable=True)           # close-fill events in window
    win_rate_7d = Column(RATIO, nullable=True)           # 0..1; NULL when trades_7d < 10
    avg_hold_minutes = Column(Integer, nullable=True)    # NULL when <5 matched open->close pairs
    avg_trade_notional = Column(USD, nullable=True)      # mean |px*sz| of close fills
    maker_ratio = Column(RATIO, nullable=True)           # share of fills NOT crossed (maker)
    realized_pnl_7d = Column(USD, nullable=True)         # sum closedPnl (excl. fees)
    sample_fills = Column(Integer, nullable=True)        # fills in window (page-capped => sample)
    sample_capped = Column(Boolean, nullable=False, default=False)  # hit the page cap => partial
    cursor_ts = Column(BigInteger, nullable=True)        # ms of newest fill fetched (delta fetch)
    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Tier-2 Part D (migration v13) — quality metrics from the same fills:
    profit_factor = Column(RATIO, nullable=True)         # gross wins / gross losses; 999.99 = no losses in window; NULL when no closes
    avg_win = Column(USD, nullable=True)                 # mean positive closedPnl
    avg_loss = Column(USD, nullable=True)                # mean |negative closedPnl|
    avg_win_loss_ratio = Column(RATIO, nullable=True)    # avg_win / avg_loss
    max_drawdown_7d = Column(USD, nullable=True)         # peak-to-trough of cumulative realized PnL, USD
    max_drawdown_pct_7d = Column(RATIO, nullable=True)   # dd / account value (when AV known)
    flip_accuracy = Column(RATIO, nullable=True)         # share of FLIPs followed by >=0.5% move within 24h (Part-A prices); NULL until history
    flip_n = Column(Integer, nullable=True)              # FLIPs evaluated


class HlFillEvent(Base):
    """Rolling raw-fill store backing trader_fill_stats (migration v6): the
    hourly sampler appends only NEW fills (cursor delta) and stats recompute
    over the true 7-day window. Pruned to 8 days + newest N per wallet."""
    __tablename__ = "hl_fill_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42), nullable=False)
    ts_ms = Column(BigInteger, nullable=False)           # venue fill time (ms UTC)
    coin = Column(String(20), nullable=False)
    dir = Column(String(20), nullable=True)              # 'Open Long', 'Close Short', flips…
    px = Column(PRICE, nullable=True)
    sz = Column(SIZE, nullable=True)
    closed_pnl = Column(USD, nullable=True)
    crossed = Column(Boolean, nullable=True)             # True = taker
    tid = Column(String(40), nullable=True)              # venue fill id (dedupe)

    __table_args__ = (
        Index("ix_hl_fill_wallet_ts", "wallet_address", "ts_ms"),
        UniqueConstraint("wallet_address", "tid", name="uq_hl_fill_wallet_tid"),
    )
