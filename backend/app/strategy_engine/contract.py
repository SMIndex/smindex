"""Typed API contract for the Strategies UI (prompt Part 4). pydantic models.

Every condition in each strategy's spec §5 maps to a `SignalCondition` entry
(name / live value / threshold / met / warming) so the UI can render
"82nd pct (need 60) · warming: 40 of 200 bars". Everything is read from the
strat_* tables the worker writes on every evaluation.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class StrategyState(BaseModel):
    id: str
    name: str
    requested_mode: str            # off | paper | live
    effective_mode: str            # off | paper | live (computed server-side)
    effective_reason: Optional[str] = None
    rolling20_pf: Optional[float] = None
    kill_switch_tripped: bool = False
    waiting_for_sentence: Optional[str] = None
    last_eval_ts: Optional[int] = None
    parameter_count: int = 0
    strategy_implemented: bool = False
    # evaluation / paper evidence (all counted from strat_signals / strat_trades)
    evals_24h: int = 0                 # evaluations logged in the last 24h
    setups_24h: int = 0                # evaluations where a direction was identified
    fires_24h: int = 0
    trades_30d: int = 0                # closed paper trades, last 30d
    win_rate_30d: Optional[float] = None
    pf_30d: Optional[float] = None
    net_30d: Optional[float] = None
    open_positions: int = 0
    # fill rate = fills / fires, all time (owner ask 2026-09-04: pullback s05 vs chase s05c)
    fires_total: int = 0               # strat_signals fired=1
    fills_total: int = 0               # strat_trades with a fill_ts (maker + taker)
    fills_taker: int = 0               # chase entries that had to cross
    entries_pending: int = 0           # resting, not yet filled
    entries_unfilled: int = 0          # TTL-cancelled without a fill
    fill_rate: Optional[float] = None  # fills_total / fires_total (None when no fires)
    warming: list[str] = []            # latest "Cond: warming: M of N" labels (union over assets)
    labels: dict = {}                  # latest labels e.g. {"cohort": "7d metrics", "liq_coverage": "partial"}
    model: Optional[str] = None        # 'M1'..'M6' for the heuristic-mind models (docs 10–16); None for 01–06


class RiskState(BaseModel):
    equity_usd: Optional[float] = None
    daily_realized_pnl: float = 0.0
    daily_cap_hit_until: Optional[int] = None
    consecutive_losses: int = 0
    paused_until: Optional[int] = None
    open_positions_count: int = 0
    paper_equity_start: float = 0.0    # STRATEGY_PAPER_EQUITY_USD
    paper_pnl_total: float = 0.0       # sum of pnl_net over closed paper trades
    paper_pnl_today: float = 0.0       # closed since 00:00 UTC
    paper_fees_month: float = 0.0
    paper_gross_month: float = 0.0


class SignalCondition(BaseModel):
    name: str
    subline: Optional[str] = None
    value: Optional[str] = None         # live value, e.g. "82nd pct"
    threshold: Optional[str] = None     # e.g. "need 60"
    met: Optional[bool] = None          # None = not yet evaluated
    warming: Optional[str] = None       # "warming: M of N bars" | "unavailable (not in score)" | None


class Signal(BaseModel):
    id: int
    ts: int
    strategy: str
    asset: str
    venue: str
    mode: str
    direction: Optional[str] = None
    regime_score: Optional[float] = None
    bias_score: Optional[float] = None
    trigger_score: Optional[float] = None
    timing_score: Optional[float] = None
    total_score: Optional[float] = None
    fired: bool = False
    reason: Optional[str] = None
    conditions: list[SignalCondition] = []
    warming: list[str] = []
    labels: dict = {}
    risk_note: Optional[str] = None
    paper_fill: bool = False
    fire_threshold: Optional[float] = None
    components: dict = {}               # regime/bias/trigger/timing/... component scores
    intents: list[dict] = []            # entry intents when fired: side/px/stop/target1/time_stop_h
    # Mind fields (models M1–M6, doc 10 §7.1) — None for strategies 01–06
    model: Optional[str] = None
    level_type: Optional[str] = None
    level_price: Optional[float] = None
    day_type: Optional[str] = None
    raw_conviction: Optional[float] = None
    conviction: Optional[float] = None
    size_tier: Optional[str] = None     # full | half | none
    reasons: list[MindReason] = []      # one row per Mind reason: name, strength, weight, contribution
    vetoes: list[MindVeto] = []
    multipliers: list[MindMultiplier] = []
    thesis: Optional[str] = None
    setup: dict = {}                    # the detected setup (level, wick, OB, ...) as the model saw it


class MindReason(BaseModel):
    key: str
    description: Optional[str] = None
    strength: float = 0.0
    weight: float = 1.0
    contribution: float = 0.0           # strength × weight / Σweights


class MindVeto(BaseModel):
    key: str
    text: Optional[str] = None
    hit: bool = False


class MindMultiplier(BaseModel):
    key: str
    multiplier: float = 1.0


class MindWeightRow(BaseModel):
    reason_key: str
    weight: float
    reason_text: Optional[str] = None
    updated_at: Optional[int] = None


class MindCalibrationRow(BaseModel):
    week: str
    bucket: str
    trades: int = 0
    win_rate: Optional[float] = None
    mean_r: Optional[float] = None


class MindBreakdown(BaseModel):
    """Detail page → Breakdown tab (doc 17 step 5): live weights, calibration
    table by conviction bucket, weight history (mind-learn changelog rows)."""
    model: str
    weights: list[MindWeightRow] = []
    calibration: list[MindCalibrationRow] = []
    weight_history: list[ChangelogEntry] = []
    learn_flags: dict = {}
    trades_closed: int = 0
    mean_r: Optional[float] = None
    r_by_tier: dict = {}                # size_tier → {trades, mean_r, win_rate}
    normalisers: list["NormaliserRow"] = []   # spec v1.1 Part C: calibrated liquidation normalisers per coin
    # spec v1.3 Part 5: outcome splits (stop floor D-88 every model; reclaim type D-89 M1/M5)
    # and the post-only execution stats (D-87)
    splits: list["OutcomeSplit"] = []
    execution: Optional["ExecutionStats"] = None


class OutcomeSplit(BaseModel):
    group: str                          # stop_floor | reclaim_type
    label: str
    trades: int = 0
    win_rate: Optional[float] = None
    mean_r: Optional[float] = None


class ExecutionStats(BaseModel):
    takes: int = 0                      # paper model entries placed (rows carrying the v1.3 column)
    requoted: int = 0                   # rejected as crossing and re-quoted one tick inside (D-87)
    requoted_fraction: Optional[float] = None
    requoted_filled: int = 0
    fill_minus_original: Optional[float] = None      # mean(actual fill − original limit), filled re-quoted trades
    fill_minus_original_bps: Optional[float] = None


class NormaliserRow(BaseModel):
    coin: str
    key: str                            # liq_5m_p90_long | liq_5m_p90_short | band_p80 | live_coverage
    value: Optional[float] = None       # None = not calibrated → model uses the fixed OI fraction
    sample_count: int = 0
    window_days: Optional[float] = None
    computed_at: Optional[int] = None
    note: Optional[str] = None
    used_by: str = ""                   # which of this model's inputs the value normalises


class AlertPref(BaseModel):
    model: str
    enabled: bool = True


class AlertPrefsPatch(BaseModel):
    prefs: list[AlertPref]


class SignalRow(BaseModel):
    """One row of the global Recent-signals list: consecutive identical
    evaluations (same strategy/asset/fired/reason) collapsed with a count."""
    ts_first: int
    ts_last: int
    count: int
    strategy: str
    asset: str
    direction: Optional[str] = None
    fired: bool = False
    reason: Optional[str] = None
    total_score: Optional[float] = None
    warming: list[str] = []
    paper_fill: bool = False


class StratStats(BaseModel):
    trades: int = 0
    win_rate: Optional[float] = None
    pf: Optional[float] = None
    expectancy: Optional[float] = None
    max_dd: Optional[float] = None
    fee_drag: Optional[float] = None    # fees / |gross|
    net: float = 0.0


class BacktestMeta(BaseModel):
    generated_ts: int
    start_ms: int
    end_ms: int
    trades: int = 0
    evals: int = 0
    coverage: dict = {}


class Trade(BaseModel):
    id: int
    signal_id: Optional[int] = None
    strategy: str
    asset: str
    venue: str
    mode: str
    direction: Optional[str] = None
    entry_px: Optional[float] = None
    stop_px: Optional[float] = None
    target_px: Optional[float] = None
    size: Optional[float] = None
    leverage: Optional[float] = None
    fill_ts: Optional[int] = None
    exit_ts: Optional[int] = None
    exit_reason: Optional[str] = None
    pnl_gross: Optional[float] = None
    fees: Optional[float] = None
    pnl_net: Optional[float] = None
    mae: Optional[float] = None
    mfe: Optional[float] = None
    # model trades (M1–M6)
    model: Optional[str] = None
    expected_hold_min: Optional[int] = None
    r_multiple: Optional[float] = None
    lifecycle: dict = {}                # phase, t1/t2/t3, remaining_size, partials, trail_ref, fallback...
    in_trade_checks: list[dict] = []    # one row per closed 15m: failing checks, streak, progress_r


class Parameter(BaseModel):
    key: str
    value: Optional[str] = None
    unit: Optional[str] = None
    default: Optional[str] = None


class ChangelogEntry(BaseModel):
    ts: int
    strategy_id: Optional[str] = None
    diff: Optional[dict] = None
    reason: Optional[str] = None
    wallet: Optional[str] = None


class FeedHealth(BaseModel):
    feed: str
    coverage: str
    rows: int
    last_ts: Optional[int] = None
    age_s: Optional[float] = None
    # Per-source staleness threshold in seconds (Part A.1) — the UI compares
    # age_s to THIS, never a hardcoded value. None = exempt (e.g. partial-coverage
    # liquidations, which are honestly sparse, not stale).
    stale_threshold_s: Optional[int] = None
    stale: bool = False


class StrategyDetail(BaseModel):
    state: StrategyState
    parameters: list[Parameter] = []
    conditions: list[SignalCondition] = []     # the spec §5 rule list (grey until eval)
    recent_signals: list[Signal] = []
    open_positions: list[Trade] = []
    recent_trades: list[Trade] = []
    latest: dict[str, Signal] = {}             # latest evaluation per asset (conditions carry live values + warming)
    stats: dict[str, StratStats] = {}          # "7d" | "30d" | "90d" | "all"
    backtest: Optional[BacktestMeta] = None    # cached report metadata (report text via /{sid}/backtest)


# ---- write payloads ----
class ModePatch(BaseModel):
    requested_mode: str            # off | paper | live


class ParameterPatch(BaseModel):
    key: str
    value: str
    reason: str                    # required — a changelog row is written
