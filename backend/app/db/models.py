import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, JSON, Index, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42), unique=True, nullable=False, index=True)
    username = Column(String(30), unique=True, nullable=True, index=True)
    perpl_account_id = Column(String(255), nullable=True)
    perpl_auth_token_encrypted = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# === LEGACY (copy v0) — superseded by copy v1 (app/db/copy_models.py) ===
# `leaders` identity → trader_profiles ; cached stats → trader_stats_daily.
# Still read for display names today. DO NOT build new features on this.
class Leader(Base):
    __tablename__ = "leaders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    wallet_address = Column(String(42), nullable=False)
    display_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    total_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    pnl_total = Column(Float, default=0.0)
    pnl_7d = Column(Float, default=0.0)
    pnl_30d = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    avg_leverage = Column(Float, default=0.0)
    followers_count = Column(Integer, default=0)
    registered_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_leaders_pnl_total", "pnl_total"),
        Index("ix_leaders_pnl_7d", "pnl_7d"),
    )


# === LEGACY/DEAD (copy v0) — unused by v1. Only the unwired copy_engine.py /
# leader_stats.py reference it. Superseded by copy_subscriptions. Scheduled for removal. ===
class FollowerConfig(Base):
    __tablename__ = "follower_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False)
    allocation_usd = Column(Float, nullable=False)
    max_leverage = Column(Float, default=50.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_follower_leader", "follower_id", "leader_id", unique=True),
        Index("ix_follower_configs_leader", "leader_id"),
    )


# === LEGACY/DEAD (copy v0) — unused by v1. Superseded by leader_trade_events.
# Scheduled for removal. ===
class LeaderTrade(Base):
    __tablename__ = "leader_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False, index=True)
    market_id = Column(Integer, nullable=False)
    side = Column(String(4), nullable=False)
    size = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    leverage = Column(Float, nullable=False)
    is_close = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    raw_fill = Column(JSON, nullable=True)


# === LEGACY/DEAD (copy v0) — unused by v1. Superseded by copy_orders.
# Scheduled for removal. ===
class CopyTradeExecution(Base):
    __tablename__ = "copy_trade_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    leader_trade_id = Column(Integer, nullable=False)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False)
    market_id = Column(Integer, nullable=False)
    side = Column(String(4), nullable=False)
    intended_size = Column(Float, nullable=False)
    actual_size = Column(Float, nullable=True)
    intended_price = Column(Float, nullable=False)
    actual_price = Column(Float, nullable=True)
    leverage = Column(Float, nullable=False)
    status = Column(String(20), default="submitted")
    error = Column(Text, nullable=True)
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow)
    filled_at = Column(DateTime, nullable=True)
    latency_ms = Column(Float, nullable=True)


class LiquidationSnapshot(Base):
    __tablename__ = "liquidation_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer, nullable=False, index=True)
    current_price = Column(Float, nullable=False)
    bins = Column(JSON, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class WhaleAlert(Base):
    __tablename__ = "whale_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer, nullable=False)
    alert_type = Column(String(20), nullable=False)
    side = Column(String(10), nullable=True)
    size_usd = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    severity = Column(String(10), nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)


class FundingHistory(Base):
    __tablename__ = "funding_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer, nullable=False)
    funding_rate = Column(Float, nullable=False)
    mark_price = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_funding_market_ts", "market_id", "timestamp"),
    )


class OrderFlowSnapshot(Base):
    __tablename__ = "order_flow_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(Integer, nullable=False)
    bid_vol = Column(Float, nullable=False)
    ask_vol = Column(Float, nullable=False)
    delta = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_flow_market_ts", "market_id", "timestamp"),
    )


class TraderActivity(Base):
    __tablename__ = "trader_activity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")  # migration v2
    activity_type = Column(String(10), nullable=False)  # entry/exit
    wallet_address = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(20), nullable=False)  # 20: HL symbols like xyz:BRENTOIL
    side = Column(String(10), nullable=False)
    size = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_trader_activity_ts", "timestamp"),
        Index("ix_trader_activity_wallet", "wallet_address"),
    )


# === LEGACY/DEAD (copy v0) — nothing writes this table; /copy/history always
# returns empty. Superseded by copy_orders + audit_logs. Scheduled for removal. ===
class CopyTradeLog(Base):
    __tablename__ = "copy_trade_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_wallet = Column(String(42), nullable=False, index=True)
    leader_wallet = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    leverage = Column(Float, nullable=False)
    amount_usd = Column(Float, nullable=False)
    status = Column(String(20), default="submitted")  # submitted/filled/failed
    error = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)


# === LEGACY (copy v0) — superseded by copy v1. This table CONFLATES watch +
# copy config + auto_copy. v1 splits it into watchlists (watch) and
# copy_subscriptions (copy). Still active today; DO NOT extend. The backfill
# script migrate_copy_v1.py splits this into the two v1 tables. ===
class WalletFollow(Base):
    """Follow a trader by wallet address — works with live leaderboard data."""
    __tablename__ = "wallet_follows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    leader_wallet = Column(String(42), nullable=False)
    allocation_usd = Column(Float, nullable=False, default=10.0)
    max_leverage = Column(Float, nullable=False, default=5.0)
    auto_copy = Column(Boolean, default=False)
    sl_pct = Column(Float, nullable=True)  # auto SL % from entry
    tp_pct = Column(Float, nullable=True)  # auto TP % from entry
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_wallet_follow_unique", "follower_wallet", "leader_wallet", unique=True),
    )


class StopOrder(Base):
    """Stop-loss and take-profit orders — monitored by sl_tp_service."""
    __tablename__ = "stop_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    wallet_address = Column(String(42), nullable=False, index=True)
    market_id = Column(Integer, nullable=False)
    side = Column(String(5), nullable=False)  # long or short (position side)
    order_type = Column(String(4), nullable=False)  # sl or tp
    trigger_price = Column(Float, nullable=False)
    size = Column(Float, nullable=True)  # null = close full position
    status = Column(String(20), default="active")  # active, triggered, cancelled, failed
    source = Column(String(20), default="manual")  # manual, copy_trade, auto_copy
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    triggered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_stop_orders_active", "status", "market_id"),
    )


# === LEGACY/DEAD (copy v0) — the auto-copy queue. Written by trader_tracker but
# NEVER executed (no consumer). v1 has no live auto-copy. Scheduled for removal. ===
class PendingCopy(Base):
    __tablename__ = "pending_copies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_wallet = Column(String(42), nullable=False, index=True)
    leader_wallet = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    allocation_usd = Column(Float, nullable=False)
    max_leverage = Column(Float, nullable=False)
    queued_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_pending_copy_follower_market", "follower_wallet", "market_id", "leader_wallet", unique=True),
    )


class EquitySnapshot(Base):
    """Periodic snapshot of a user's account equity for equity curve chart."""
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String(42), nullable=False)
    equity = Column(Float, nullable=False)
    balance = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False, default=0)
    margin_used = Column(Float, nullable=False, default=0)
    position_count = Column(Integer, nullable=False, default=0)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_equity_snap_wallet_ts", "wallet_address", "timestamp"),
    )


# === LEGACY (copy v0) — active today (manual copies write here). In copy v1 this
# is rebuilt to add subscription_id / mode (paper|live) / leader_event_id /
# close_reason. DO NOT extend this model in place; the v1 rebuild happens in a
# later phase. ===
class CopyPosition(Base):
    """Tracks each copied position — who copied whom, with lifecycle tracking."""
    __tablename__ = "copy_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    follower_wallet = Column(String(42), nullable=False)
    leader_wallet = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    leverage = Column(Float, nullable=False)
    allocation_usd = Column(Float, nullable=False)
    status = Column(String(20), default="open")  # open/closed/liquidated
    close_price = Column(Float, nullable=True)
    realized_pnl = Column(Float, nullable=True)
    source = Column(String(20), default="manual")  # manual/auto_copy/telegram
    opened_at = Column(DateTime, default=datetime.datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_copy_pos_follower_status", "follower_wallet", "status"),
        Index("ix_copy_pos_leader", "leader_wallet"),
        Index("ix_copy_pos_open", "follower_wallet", "leader_wallet", "market_id", "status"),
    )


class TelegramLink(Base):
    """Links a user's Telegram chat to their account for trade notifications."""
    __tablename__ = "telegram_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    chat_id = Column(String(50), nullable=True)
    link_code = Column(String(64), nullable=True)
    link_code_expires = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=False)
    linked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_telegram_chat", "chat_id"),
        Index("ix_telegram_link_code", "link_code"),
    )


class PriceAlertDB(Base):
    """Server-side price alerts with Telegram notification support."""
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wallet_address = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    condition = Column(String(10), nullable=False)  # above / below
    target_price = Column(Float, nullable=False)
    status = Column(String(20), default="active")  # active / triggered
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    triggered_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_price_alerts_active", "status", "market_id"),
        Index("ix_price_alerts_user", "user_id"),
    )


class TradeJournal(Base):
    """Trade journal entries for tracking and reviewing trades."""
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    rating = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_journal_user", "user_id"),
        Index("ix_journal_user_market", "user_id", "market_id"),
    )


class MCPToken(Base):
    """API tokens for MCP (Model Context Protocol) clients — Claude Desktop / Code / Web.

    Token plaintext is shown to the user ONCE on creation; only the SHA-256 hash is
    stored. Each token belongs to one user and is used by an MCP client to authenticate
    when calling tools that need user context (e.g. get_my_positions).
    """
    __tablename__ = "mcp_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    label = Column(String(64), nullable=False)  # e.g. "Claude Desktop on macbook"
    token_hash = Column(String(64), unique=True, nullable=False, index=True)  # sha256 hex
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # absolute expiry; NULL = legacy/no-expiry
    scopes = Column(String(128), nullable=True)  # csv: "read,journal,stage"; NULL = all (legacy)

    __table_args__ = (
        Index("ix_mcp_tokens_user", "user_id"),
    )


class OrderHistory(Base):
    """Every order attempt — filled, failed, cancelled, or sitting in book."""
    __tablename__ = "order_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wallet_address = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    direction = Column(String(20), nullable=False)   # Open Long, Close Short, etc.
    order_type = Column(String(10), nullable=False)   # market / limit
    size = Column(Float, nullable=False)
    filled_size = Column(Float, nullable=True)
    order_value = Column(Float, nullable=True)        # notional USD
    price = Column(Float, nullable=True)              # order price
    fill_price = Column(Float, nullable=True)         # actual fill price
    reduce_only = Column(Boolean, default=False)
    status = Column(String(20), nullable=False)       # filled/failed/cancelled/open/expired
    order_id = Column(String(50), nullable=True)      # Perpl order ID
    fee = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    source = Column(String(20), default="manual")
    error = Column(Text, nullable=True)
    raw_response = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_order_hist_user_ts", "user_id", "created_at"),
        Index("ix_order_hist_wallet", "wallet_address"),
    )


class TradeHistory(Base):
    """Every fill/trade for a user — captured from frontend on order fill."""
    __tablename__ = "trade_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wallet_address = Column(String(42), nullable=False)
    market_id = Column(Integer, nullable=False)
    symbol = Column(String(10), nullable=False)
    side = Column(String(10), nullable=False)       # long/short
    action = Column(String(10), nullable=False)      # open/close
    order_type = Column(String(10), nullable=False)  # market/limit
    size = Column(Float, nullable=False)
    price = Column(Float, nullable=False)            # fill price
    leverage = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    notional = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)               # realized PnL (for close trades)
    order_id = Column(Integer, nullable=True)        # Perpl order ID
    raw_response = Column(JSON, nullable=True)       # full Perpl WS response
    source = Column(String(20), default="manual")    # manual/copy_trade/auto_copy/mcp
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_trade_hist_user_ts", "user_id", "created_at"),
        Index("ix_trade_hist_wallet_ts", "wallet_address", "created_at"),
        Index("ix_trade_hist_market", "market_id"),
    )


class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # migration v2: source exchange ('perpl' | 'hl'); default keeps every
    # pre-existing row and every legacy write Perpl-scoped.
    exchange = Column(String(20), nullable=False, default="perpl", server_default="perpl")
    wallet_address = Column(String(42), nullable=False)
    rank = Column(Integer, nullable=False)
    pnl_total = Column(Float, nullable=False)
    roi = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    period = Column(String(10), nullable=False)  # perpl: all/day · hl: all/day/week/month
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        Index("ix_lb_snap_ts", "timestamp"),
        Index("ix_lb_snap_exch_period_ts", "exchange", "period", "timestamp"),
        Index("ix_lb_snap_wallet", "wallet_address"),
    )
