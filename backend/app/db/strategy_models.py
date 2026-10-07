"""Strategy engine data-layer tables (doc 00 §3, prompt Part 2).

All prefixed `strat_`. Auto-created via SQLAlchemy `create_all` (the project's
migration mechanism for NEW tables; conceptual migration v14). `ts` columns are
UTC epoch milliseconds (BigInteger) unless the column name says otherwise.

Retention jobs live in the worker (Part 3); this module is schema only. No
fabricated data — every row is written from a real feed.
"""
import datetime

from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, JSON, String, Text, DateTime, Index, UniqueConstraint

from app.db.database import Base


class StratCandle(Base):
    """15m/1h/4h OHLCV from the HL `candle` ws + `candleSnapshot` backfill.
    Retention (Part 3): all 15m for 18 months; 1h/4h indefinitely."""
    __tablename__ = "strat_candles"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    venue = Column(String(20), nullable=False, default="hl", server_default="hl")
    coin = Column(String(20), nullable=False)
    tf = Column(String(8), nullable=False)              # '15m' | '1h' | '4h'
    ts = Column(BigInteger, nullable=False)             # candle close, epoch ms
    o = Column(Float, nullable=False)
    h = Column(Float, nullable=False)
    l = Column(Float, nullable=False)
    c = Column(Float, nullable=False)
    v = Column(Float, nullable=False)
    __table_args__ = (
        UniqueConstraint("venue", "coin", "tf", "ts", name="uq_strat_candles_key"),
        Index("ix_strat_candles_coin_tf_ts", "coin", "tf", "ts"),
    )


class StratTrades1m(Base):
    """Per-minute taker flow aggregated in-memory from the HL `trades` ws.
    Raw ticks are NOT stored. Retention 18 months."""
    __tablename__ = "strat_trades_1m"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    ts = Column(BigInteger, nullable=False)             # minute bucket start, epoch ms
    taker_buy_notional = Column(Float, nullable=False, default=0.0)
    taker_sell_notional = Column(Float, nullable=False, default=0.0)
    count = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("coin", "ts", name="uq_strat_trades_1m_key"),
        Index("ix_strat_trades_1m_coin_ts", "coin", "ts"),
    )


class StratBook5s(Base):
    """L2 depth (notional USD within 0.1/0.3/0.5% of mid), every 5 s from `l2Book`.
    Retention 30 days full, then hourly aggregates (Part 3)."""
    __tablename__ = "strat_book_5s"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    ts = Column(BigInteger, nullable=False)
    mid = Column(Float, nullable=False)
    bid_0_1 = Column(Float, nullable=False, default=0.0)
    bid_0_3 = Column(Float, nullable=False, default=0.0)
    bid_0_5 = Column(Float, nullable=False, default=0.0)
    ask_0_1 = Column(Float, nullable=False, default=0.0)
    ask_0_3 = Column(Float, nullable=False, default=0.0)
    ask_0_5 = Column(Float, nullable=False, default=0.0)
    # D-108: quote spread (best_ask - best_bid). Nullable because rows written
    # before 2026-09-12 do not have it; the regime gate treats NULL as unknown.
    spread = Column(Float, nullable=True)
    __table_args__ = (Index("ix_strat_book_5s_coin_ts", "coin", "ts"),)


class StratOi1m(Base):
    """Per-minute OI/funding/mark context from `activeAssetCtx` (fallback: the
    existing 60 s `metaAndAssetCtxs` poll). Retention INDEFINITE — this history
    cannot be re-obtained."""
    __tablename__ = "strat_oi_1m"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    ts = Column(BigInteger, nullable=False)
    oi_notional = Column(Float, nullable=True)
    funding = Column(Float, nullable=True)
    predicted_funding = Column(Float, nullable=True)
    mark = Column(Float, nullable=True)
    oracle = Column(Float, nullable=True)
    premium = Column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("coin", "ts", name="uq_strat_oi_1m_key"),
        Index("ix_strat_oi_1m_coin_ts", "coin", "ts"),
    )


class StratFunding(Base):
    """Funding across venues (hl/binance/bybit). HL from activeAssetCtx +
    fundingHistory 30-day backfill; Binance `premiumIndex` + Bybit tickers polled
    60 s. Retention indefinite."""
    __tablename__ = "strat_funding"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    venue = Column(String(10), nullable=False)          # 'hl' | 'binance' | 'bybit'
    coin = Column(String(20), nullable=False)
    rate = Column(Float, nullable=True)
    predicted_rate = Column(Float, nullable=True)
    next_settlement_ts = Column(BigInteger, nullable=True)
    oi_notional = Column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("venue", "coin", "ts", name="uq_strat_funding_key"),
        Index("ix_strat_funding_venue_coin_ts", "venue", "coin", "ts"),
    )


class StratLiquidation(Base):
    """Liquidation-flagged fills. coverage='partial' in Phase 1 (only wallets on
    the existing <=10-slot ws tier; the docs' 2,000-wallet userFills sub violates
    the venue's 10-unique-users-per-IP limit verified in production)."""
    __tablename__ = "strat_liquidations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    coin = Column(String(20), nullable=False)
    side = Column(String(8), nullable=True)
    px = Column(Float, nullable=True)
    sz = Column(Float, nullable=True)
    notional = Column(Float, nullable=True)
    liquidated_user = Column(String(66), nullable=True)
    mark_px = Column(Float, nullable=True)
    method = Column(String(16), nullable=True)          # 'market' | 'backstop'
    coverage = Column(String(16), nullable=False, default="partial", server_default="partial")
    source = Column(String(16), nullable=True)          # NULL/'live' = HL ws fills; 'oxarchive' = 0xArchive history (v16)
    __table_args__ = (Index("ix_strat_liquidations_coin_ts", "coin", "ts"),)


class StratEvent(Base):
    """Macro calendar loaded from config/events.yaml (owner maintains it)."""
    __tablename__ = "strat_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)           # 'CPI' | 'NFP' | 'FOMC' | 'PCE' | 'ISM'
    utc_ts = Column(BigInteger, nullable=False)         # event time, epoch ms UTC
    kind = Column(String(32), nullable=True)
    __table_args__ = (Index("ix_strat_events_utc_ts", "utc_ts"),)


class StratFeedMeta(Base):
    """One row per feed carrying its coverage label + note (prompt Part 2:
    liquidations feed is coverage='partial' in a metadata row AND every consumer)."""
    __tablename__ = "strat_feed_meta"
    id = Column(Integer, primary_key=True, autoincrement=True)
    feed = Column(String(32), unique=True, nullable=False)
    coverage = Column(String(16), nullable=False, default="full", server_default="full")
    note = Column(Text, nullable=True)
    updated_ts = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


# ===================== Part 3: feature outputs, state, logs =====================

class StratRegime(Base):
    """Regime-gate result per closed 15m candle (doc 00 §4)."""
    __tablename__ = "strat_regime"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    coin = Column(String(20), nullable=False)
    allowed = Column(Boolean, nullable=False, default=False)
    score = Column(Float, nullable=True)                 # 0..1
    blockers = Column(JSON, nullable=True)               # list of active blockers
    __table_args__ = (
        UniqueConstraint("coin", "ts", name="uq_strat_regime_key"),
        Index("ix_strat_regime_coin_ts", "coin", "ts"),
    )


class StratGauge(Base):
    """Funding gauge per minute (doc 02 §13). Gauge only — no trade."""
    __tablename__ = "strat_gauge"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    coin = Column(String(20), nullable=False)
    hl_funding_8h_equiv = Column(Float, nullable=True)
    funding_z = Column(Float, nullable=True)
    crowding_level = Column(String(12), nullable=True)   # NORMAL | ELEVATED | EXTREME
    tilt = Column(Float, nullable=True)                  # -1..+1
    blocked_direction = Column(String(8), nullable=True)  # 'long' | 'short' | None
    __table_args__ = (
        UniqueConstraint("coin", "ts", name="uq_strat_gauge_key"),
        Index("ix_strat_gauge_coin_ts", "coin", "ts"),
    )


class StratBias(Base):
    """Bias score per coin every 5 minutes (doc 00 §4). components carries each
    weighted input and 'not_available' markers for basis/crowd in Phase 1."""
    __tablename__ = "strat_bias"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    coin = Column(String(20), nullable=False)
    bias_score = Column(Float, nullable=True)            # -1..+1
    components = Column(JSON, nullable=True)
    __table_args__ = (
        UniqueConstraint("coin", "ts", name="uq_strat_bias_key"),
        Index("ix_strat_bias_coin_ts", "coin", "ts"),
    )


class StratRiskState(Base):
    """Global bot risk state (doc 00 §5). Single row (id=1). No enforcement in
    Phase 1 — seeded and updated by the scheduler; strategies do not run."""
    __tablename__ = "strat_risk_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    equity_usd = Column(Float, nullable=True)
    daily_realized_pnl = Column(Float, nullable=False, default=0.0)
    daily_cap_hit_until = Column(BigInteger, nullable=True)
    consecutive_losses = Column(Integer, nullable=False, default=0)
    paused_until = Column(BigInteger, nullable=True)
    open_positions_count = Column(Integer, nullable=False, default=0)
    updated_ts = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class StratStrategyState(Base):
    """Per-strategy mode + kill-switch state (doc 00 §5). Six rows seeded off/off.
    effective_mode is COMPUTED server-side; requested_mode is the only writable
    field (Part 4)."""
    __tablename__ = "strat_strategy_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(32), unique=True, nullable=False)
    requested_mode = Column(String(8), nullable=False, default="off")     # off | paper | live
    effective_mode = Column(String(8), nullable=False, default="off")
    effective_reason = Column(String(255), nullable=True)
    rolling20_pf = Column(Float, nullable=True)
    kill_switch_tripped = Column(Boolean, nullable=False, default=False)
    waiting_for_sentence = Column(String(255), nullable=True)
    last_eval_ts = Column(BigInteger, nullable=True)
    updated_ts = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class StratSignal(Base):
    """Every evaluation, fired or not (doc 00 §8 + venue + mode)."""
    __tablename__ = "strat_signals"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    strategy = Column(String(32), nullable=False)
    asset = Column(String(20), nullable=False)
    venue = Column(String(10), nullable=False, default="hl")
    mode = Column(String(8), nullable=False, default="off")
    direction = Column(String(8), nullable=True)         # long | short
    regime_score = Column(Float, nullable=True)
    bias_score = Column(Float, nullable=True)
    trigger_score = Column(Float, nullable=True)
    timing_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=True)
    fired = Column(Boolean, nullable=False, default=False)
    reason = Column(String(255), nullable=True)          # one-line reason sentence
    components = Column(JSON, nullable=True)
    # v15 (models M1–M6, doc 10 §7.1) — NULL for strategies 01–06
    model = Column(String(8), nullable=True)
    level_type = Column(String(32), nullable=True)
    level_price = Column(Float, nullable=True)
    day_type = Column(String(16), nullable=True)
    raw_conviction = Column(Float, nullable=True)
    conviction = Column(Float, nullable=True)
    size_tier = Column(String(8), nullable=True)
    reasons_json = Column(JSON, nullable=True)
    vetoes_json = Column(JSON, nullable=True)
    multipliers_json = Column(JSON, nullable=True)
    thesis = Column(Text, nullable=True)
    # v17 (spec v1.3 D-89/D-91) — M1/M5 reclaim type; NULL elsewhere
    reclaim_candles = Column(Integer, nullable=True)
    confirmation_used = Column(Boolean, nullable=True)
    __table_args__ = (Index("ix_strat_signals_strategy_ts", "strategy", "ts"),)


class StratTrade(Base):
    """Executed trade outcomes (doc 00 §8 + venue + mode). None in Phase 1."""
    __tablename__ = "strat_trades"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    signal_id = Column(BigInteger, nullable=True)
    strategy = Column(String(32), nullable=False)
    asset = Column(String(20), nullable=False)
    venue = Column(String(10), nullable=False, default="hl")
    mode = Column(String(8), nullable=False, default="off")
    direction = Column(String(8), nullable=True)
    entry_px = Column(Float, nullable=True)
    stop_px = Column(Float, nullable=True)
    target_px = Column(Float, nullable=True)
    size = Column(Float, nullable=True)
    leverage = Column(Float, nullable=True)
    fill_ts = Column(BigInteger, nullable=True)
    exit_ts = Column(BigInteger, nullable=True)
    exit_reason = Column(String(128), nullable=True)   # v14: s05c chase marker is 69 chars
    pnl_gross = Column(Float, nullable=True)
    fees = Column(Float, nullable=True)
    pnl_net = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    # v15 (models M1–M6, doc 10 §7.1) — NULL for strategies 01–06
    model = Column(String(8), nullable=True)
    expected_hold_min = Column(Integer, nullable=True)
    r_multiple = Column(Float, nullable=True)
    in_trade_checks_json = Column(JSON, nullable=True)
    lifecycle_json = Column(JSON, nullable=True)
    # v17 (spec v1.3 D-87/D-88/D-91) — model trades: stop floor + post-only rejection/re-quote
    stop_floor_applied = Column(Boolean, nullable=True)
    entry_requoted = Column(Boolean, nullable=True)
    entry_px_orig = Column(Float, nullable=True)
    __table_args__ = (Index("ix_strat_trades_strategy_ts", "strategy", "fill_ts"),)


class StratParameter(Base):
    """Per-strategy parameters seeded from docs 01–06 (Part 3.8). Writable via
    Part 4 (reason required, changelog row written)."""
    __tablename__ = "strat_parameters"
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(String(32), nullable=False)
    key = Column(String(64), nullable=False)
    value = Column(String(64), nullable=True)
    unit = Column(String(32), nullable=True)
    default = Column(String(64), nullable=True)
    updated_ts = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("strategy_id", "key", name="uq_strat_parameters_key"),
        Index("ix_strat_parameters_strategy", "strategy_id"),
    )


class StratChangelog(Base):
    """Parameter/mode change audit (Part 3.8 / Part 4)."""
    __tablename__ = "strat_changelog"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    strategy_id = Column(String(32), nullable=True)
    diff = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    wallet = Column(String(66), nullable=True)
    __table_args__ = (Index("ix_strat_changelog_ts", "ts"),)


class StratTelegramOutbox(Base):
    """Cross-process Telegram outbox (prompt Part 5). The worker WRITES rows; the
    existing Telegram queue worker in the API process DRAINS them (same queue,
    throttle, dedupe — no second bot). chat_id NULL = resolve recipients at drain
    time (admin-linked chats only in Phase 1 — no per-user preference store)."""
    __tablename__ = "strat_telegram_outbox"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    ts = Column(BigInteger, nullable=False)
    kind = Column(String(32), nullable=True)             # e.g. 'gauge_level_change'
    strategy_id = Column(String(32), nullable=True)
    coin = Column(String(20), nullable=True)
    message = Column(Text, nullable=False)
    dedupe_key = Column(String(128), nullable=True)
    chat_id = Column(String(64), nullable=True)
    sent = Column(Boolean, nullable=False, default=False)
    sent_ts = Column(BigInteger, nullable=True)
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_strat_outbox_dedupe"),
        Index("ix_strat_outbox_sent", "sent"),
    )


# ---------------------------------------------------------------------------
# Models M1–M6 / heuristic Mind (docs 10–16, migration v15). Columns added to
# strat_signals / strat_trades live in app/db/migrations_models.py (guarded
# ALTERs — create_all cannot add columns); the new tables below are create_all.
# ---------------------------------------------------------------------------
class MindWeight(Base):
    """Current weight per Mind reason (doc 10 §5). Seeded from each model's
    reason table on first run; changed only by mind/learn.py (weekly)."""
    __tablename__ = "mind_weights"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(8), nullable=False)
    reason_key = Column(String(48), nullable=False)
    weight = Column(Float, nullable=False)
    updated_at = Column(BigInteger, nullable=False)
    reason_text = Column(Text, nullable=True)
    __table_args__ = (Index("ix_mind_weights_model", "model", "reason_key"),)


class MindCalibration(Base):
    """Weekly calibration per conviction bucket (doc 10 §4.6)."""
    __tablename__ = "mind_calibration"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(8), nullable=False)
    week = Column(String(10), nullable=False)             # ISO week 'YYYY-Www'
    bucket = Column(String(16), nullable=False)           # '0.55-0.65' | '0.65-0.75' | '0.75+'
    trades = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=True)
    mean_r = Column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("model", "week", "bucket", name="uq_mind_calibration_key"),)


class MindModelState(Base):
    """Per-model/coin frozen state surviving restarts (Asia range, weekly
    levels, day type, attempt counters). DECISIONS.md D-07."""
    __tablename__ = "mind_model_state"
    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(8), nullable=False)
    coin = Column(String(20), nullable=False)
    key = Column(String(48), nullable=False)
    value_json = Column(JSON, nullable=True)
    updated_ts = Column(BigInteger, nullable=False)
    __table_args__ = (UniqueConstraint("model", "coin", "key", name="uq_mind_model_state_key"),)


class StratAlertPref(Base):
    """Per-wallet Telegram alert switch per model (doc 17 step 6 Settings
    checkboxes). Absent row = enabled."""
    __tablename__ = "strat_alert_prefs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet = Column(String(66), nullable=False)
    model = Column(String(8), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    updated_ts = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    __table_args__ = (UniqueConstraint("wallet", "model", name="uq_strat_alert_prefs_key"),)


# ---------------------------------------------------------------------------
# Spec v1.1 (2026-09-06, D-64…): liquidation history, calibration, replay data.
# All create_all; the `source` column on strat_liquidations is migration v16.
# ---------------------------------------------------------------------------
class StratLiqMapHist(Base):
    """Reconstructed liquidation map at 15m boundaries (Part B): per boundary,
    side and 0.25% band the notional of positions liquidated within the
    forward horizon (source='fills') or the 0xArchive projected-levels snapshot
    (source='levels'). D-65."""
    __tablename__ = "strat_liq_map_hist"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    ts = Column(BigInteger, nullable=False)             # 15m boundary, epoch ms
    side = Column(String(8), nullable=False)            # side of the positions in the band
    band_px = Column(Float, nullable=False)             # notional-weighted band price
    band_idx = Column(Integer, nullable=False)          # int(px // (mid * 0.0025)) on the fixed grid
    notional = Column(Float, nullable=False)
    count = Column(Integer, nullable=False, default=0)
    source = Column(String(16), nullable=False)         # 'fills' | 'levels'
    __table_args__ = (
        UniqueConstraint("coin", "ts", "side", "band_idx", "source", name="uq_strat_liq_map_hist_key"),
        Index("ix_strat_liq_map_hist_coin_ts", "coin", "ts"),
    )


class StratCalibration(Base):
    """Per-coin liquidation normalisers (Part C, structure/calibration.py):
    liq_5m_p90_long / liq_5m_p90_short / band_p80 / live_coverage."""
    __tablename__ = "strat_calibration"
    id = Column(Integer, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    key = Column(String(32), nullable=False)
    value = Column(Float, nullable=True)
    computed_at = Column(BigInteger, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    window_days = Column(Float, nullable=True)          # D-66: the window actually used
    note = Column(String(255), nullable=True)
    __table_args__ = (UniqueConstraint("coin", "key", name="uq_strat_calibration_key"),)


class StratCalibrationHist(Base):
    """Point-in-time calibration history (spec v1.2 Part 4, D-76): one row per
    (coin, key, as_of day boundary) computed from data strictly BEFORE as_of
    over a trailing window of 30..180 days. Replay lookups read the latest
    as_of <= boundary; the live daily recompute appends today's row."""
    __tablename__ = "strat_calibration_hist"
    id = Column(Integer, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    key = Column(String(32), nullable=False)
    as_of = Column(BigInteger, nullable=False)           # 00:00 UTC day boundary, epoch ms
    value = Column(Float, nullable=True)
    computed_at = Column(BigInteger, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    window_days = Column(Float, nullable=True)
    note = Column(String(255), nullable=True)
    __table_args__ = (UniqueConstraint("coin", "key", "as_of", name="uq_strat_calibration_hist_key"),
                      Index("ix_strat_calibration_hist_lookup", "coin", "key", "as_of"))


class StratReplayCandle(Base):
    """Replay-only candles (Part D) — Binance USDT-M perpetual klines, kept
    apart from strat_candles (the live HL store). ts = candle close ms."""
    __tablename__ = "strat_replay_candles"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(16), nullable=False)         # 'binance'
    coin = Column(String(20), nullable=False)
    tf = Column(String(8), nullable=False)              # '15m' | '1h' | '4h' | '1d'
    ts = Column(BigInteger, nullable=False)
    o = Column(Float, nullable=False)
    h = Column(Float, nullable=False)
    l = Column(Float, nullable=False)
    c = Column(Float, nullable=False)
    v = Column(Float, nullable=False)
    __table_args__ = (
        UniqueConstraint("source", "coin", "tf", "ts", name="uq_strat_replay_candles_key"),
        Index("ix_strat_replay_candles_coin_tf_ts", "coin", "tf", "ts"),
    )


class StratReplayOI(Base):
    """Replay-only open-interest history (Part D) at the finest public
    resolution available; `source` records where it came from."""
    __tablename__ = "strat_replay_oi"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source = Column(String(16), nullable=False)         # 'binance' | 'oxarchive'
    coin = Column(String(20), nullable=False)
    ts = Column(BigInteger, nullable=False)
    oi_contracts = Column(Float, nullable=True)
    oi_notional = Column(Float, nullable=True)
    mark = Column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("source", "coin", "ts", name="uq_strat_replay_oi_key"),
        Index("ix_strat_replay_oi_coin_ts", "coin", "ts"),
    )
