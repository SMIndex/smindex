// Typed contract for the Strategies UI (mirrors backend
// app/strategy_engine/contract.py). Everything is read from the strat_* tables
// the worker writes on every evaluation; counts/stats are counted server-side.

export type StrategyMode = 'off' | 'paper' | 'live';

export interface StrategyState {
  id: string;
  name: string;
  requested_mode: StrategyMode;
  effective_mode: StrategyMode;        // computed server-side from the kill-switch ladder
  effective_reason: string | null;
  rolling20_pf: number | null;
  kill_switch_tripped: boolean;
  waiting_for_sentence: string | null;
  last_eval_ts: number | null;
  parameter_count: number;
  strategy_implemented: boolean;
  evals_24h: number;                   // evaluations logged in the last 24h
  setups_24h: number;                  // evaluations where a direction was identified
  fires_24h: number;
  trades_30d: number;                  // closed paper trades, last 30d
  win_rate_30d: number | null;
  pf_30d: number | null;
  net_30d: number | null;
  open_positions: number;
  // fill rate = fills / fires, all time (s05 pullback vs s05c chase comparison)
  fires_total: number;
  fills_total: number;
  fills_taker: number;                 // chase entries that had to cross (taker fee)
  entries_pending: number;
  entries_unfilled: number;
  fill_rate: number | null;
  warming: string[];                   // latest "Cond: warming: M of N" labels
  labels: Record<string, string>;      // e.g. { cohort: "cohort: 7d metrics …", liq_coverage: "partial" }
  model?: string | null;               // 'M1'..'M6' for the heuristic-mind models (docs 10–16); null for 01–06
}

export interface RiskState {
  equity_usd: number | null;
  daily_realized_pnl: number;
  daily_cap_hit_until: number | null;
  consecutive_losses: number;
  paused_until: number | null;
  open_positions_count: number;
  paper_equity_start: number;
  paper_pnl_total: number;
  paper_pnl_today: number;
  paper_fees_month: number;
  paper_gross_month: number;
}

export interface SignalCondition {
  name: string;
  subline?: string | null;
  value?: string | null;               // live value e.g. "82nd pct"
  threshold?: string | null;           // e.g. "need 60"
  met?: boolean | null;                // null = not yet evaluated (grey dot)
  warming?: string | null;             // "warming: M of N bars" | "unavailable (not in score)" | null
}

export interface Signal {
  id: number;
  ts: number;
  strategy: string;
  asset: string;
  venue: string;
  mode: StrategyMode;
  direction: string | null;
  regime_score: number | null;
  bias_score: number | null;
  trigger_score: number | null;
  timing_score: number | null;
  total_score: number | null;
  fired: boolean;
  reason: string | null;
  conditions: SignalCondition[];
  warming: string[];
  labels: Record<string, string>;
  risk_note: string | null;
  paper_fill: boolean;
  fire_threshold: number | null;
  components: Record<string, number | null>;
  intents: { side: string; kind: string; px: number | null; stop: number | null; target1: number | null; time_stop_h: number | null; entry_valid_min: number | null; size_mult: number | null }[];
  // Mind fields (models M1–M6, doc 10 §7.1) — absent/null for strategies 01–06
  model?: string | null;
  level_type?: string | null;
  level_price?: number | null;
  day_type?: string | null;
  raw_conviction?: number | null;
  conviction?: number | null;
  size_tier?: string | null;           // full | half | none
  reasons?: MindReason[];              // one row per Mind reason
  vetoes?: MindVeto[];
  multipliers?: MindMultiplier[];
  thesis?: string | null;
  setup?: Record<string, unknown>;     // the detected setup as the model saw it (level, wick, entry, stop, t1, t2 …)
}

export interface MindReason {
  key: string;
  description: string | null;
  strength: number;
  weight: number;
  contribution: number;                // strength × weight / Σweights
}

export interface MindVeto {
  key: string;
  text: string | null;
  hit: boolean;
}

export interface MindMultiplier {
  key: string;
  multiplier: number;
}

export interface MindWeightRow {
  reason_key: string;
  weight: number;
  reason_text: string | null;
  updated_at: number | null;
}

export interface MindCalibrationRow {
  week: string;
  bucket: string;
  trades: number;
  win_rate: number | null;
  mean_r: number | null;
}

export interface MindBreakdown {
  model: string;
  weights: MindWeightRow[];
  calibration: MindCalibrationRow[];
  weight_history: ChangelogEntry[];
  learn_flags: Record<string, unknown>;
  trades_closed: number;
  mean_r: number | null;
  r_by_tier: Record<string, { trades: number; mean_r: number; win_rate: number }>;
  normalisers: NormaliserRow[];
  // spec v1.3 Part 5: stop-floor / reclaim-type outcome splits + post-only execution stats
  splits?: OutcomeSplit[];
  execution?: ExecutionStats | null;
}

export interface OutcomeSplit {
  group: 'stop_floor' | 'reclaim_type' | string;
  label: string;
  trades: number;
  win_rate: number | null;
  mean_r: number | null;
}

export interface ExecutionStats {
  takes: number;
  requoted: number;
  requoted_fraction: number | null;
  requoted_filled: number;
  fill_minus_original: number | null;
  fill_minus_original_bps: number | null;
}

export interface NormaliserRow {
  coin: string;
  key: string;
  value: number | null;
  sample_count: number;
  window_days: number | null;
  computed_at: number | null;
  note: string | null;
  used_by: string;
}

export interface AlertPref {
  model: string;
  enabled: boolean;
}

// Global Recent-signals row: consecutive identical evaluations collapsed
export interface SignalRow {
  ts_first: number;
  ts_last: number;
  count: number;
  strategy: string;
  asset: string;
  direction: string | null;
  fired: boolean;
  reason: string | null;
  total_score: number | null;
  warming: string[];
  paper_fill: boolean;
}

export interface StratStats {
  trades: number;
  win_rate: number | null;
  pf: number | null;
  expectancy: number | null;
  max_dd: number | null;
  fee_drag: number | null;
  net: number;
}

export interface BacktestMeta {
  generated_ts: number;
  start_ms: number;
  end_ms: number;
  trades: number;
  evals: number;
  coverage: Record<string, string | null>;
}

export interface Trade {
  id: number;
  signal_id: number | null;
  strategy: string;
  asset: string;
  venue: string;
  mode: StrategyMode;
  direction: string | null;
  entry_px: number | null;
  stop_px: number | null;
  target_px: number | null;
  size: number | null;
  leverage: number | null;
  fill_ts: number | null;
  exit_ts: number | null;
  exit_reason: string | null;
  pnl_gross: number | null;
  fees: number | null;
  pnl_net: number | null;
  mae: number | null;
  mfe: number | null;
  // model trades (M1–M6)
  model?: string | null;
  expected_hold_min?: number | null;
  r_multiple?: number | null;
  lifecycle?: Record<string, unknown>;   // phase, t1/t2/t3, remaining_size, partials, trail_ref …
  in_trade_checks?: Record<string, unknown>[];
}

export interface Parameter {
  key: string;
  value: string | null;
  unit: string | null;
  default: string | null;
}

export interface ChangelogEntry {
  ts: number;
  strategy_id: string | null;
  diff: Record<string, unknown> | null;
  reason: string | null;
  wallet: string | null;
}

export interface FeedHealth {
  feed: string;
  coverage: string;                    // 'full' | 'partial'
  rows: number;
  last_ts: number | null;
  age_s: number | null;
  stale_threshold_s?: number | null;   // per-source, from the payload (never hardcoded in UI)
  stale?: boolean;                     // server-computed against that threshold
}

export interface StrategyDetail {
  state: StrategyState;
  parameters: Parameter[];
  conditions: SignalCondition[];       // spec §5 rule list (grey until first eval)
  recent_signals: Signal[];
  open_positions: Trade[];
  recent_trades: Trade[];
  latest: Record<string, Signal>;      // latest evaluation per asset
  stats: Record<string, StratStats>;   // '7d' | '30d' | '90d' | 'all'
  backtest: BacktestMeta | null;       // cached report metadata
}
