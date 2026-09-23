// Smart-money analytics API client (Phase 1). Touches ONLY /api/analytics/*.
// Every payload carries computed_at + resolution — render them, never hide them.
import api from '@/lib/api';

export interface CohortSummary {
  size: number;
  mm_flagged: number;
  mm_unknown: number;
  hedger_flagged?: number;
  hedger_unknown?: number;
  refreshed_at: number | null;
}

export interface VariantRollup {
  wallets_long: number;
  wallets_short: number;
  notional_long: number;
  notional_short: number;
  avg_lev_long: number | null;
  avg_lev_short: number | null;
  upnl_long: number;
  upnl_short: number;
  cohort_oi: number;
  low_sample: boolean;
  age: { dated: number | null; undated: number | null; fresh_pct_24h: number | null };
}

export interface AssetRollup {
  asset: string;
  core?: VariantRollup;
  all?: VariantRollup;
  venue?: { oi_usd: number | null; funding_hourly: number | null; mark: number | null };
}

export interface AssetsResponse {
  assets: AssetRollup[];
  computed_at: string | null;
  resolution: string;
  cohort: CohortSummary;
}

export interface EntryBucket { px_lo: number; px_hi: number; count: number; notional: number; quality?: number; far?: boolean }
export interface LevBucket { lo: number; hi: number; count: number }

// ---- Phase 3: depth ---------------------------------------------------------

export interface Conviction { pct: number; flagged: boolean }

export interface CrowdMember {
  wallet: string; entry_px: number; notional: number;
  leverage?: number | null;
  conviction?: Conviction | null; rank?: number | null;
}

export interface CrowdCluster {
  side: 'long' | 'short';
  entry_lo: number; entry_hi: number;
  wallet_count: number; notional: number;
  entity_count?: number;   // Tier-2 C2: one vote per same-entity cluster
  wallets: CrowdMember[];
}

export interface TriggerClusters {
  claimed: boolean;
  observed: number;
  wallets_checked: number;
  wallets_with_triggers: number;
  min_claim: number;
  window_days: number;
  buckets: { below_mark: { px_lo: number; px_hi: number; count: number }[];
             above_mark: { px_lo: number; px_hi: number; count: number }[] };
}

export type AccountBand = 'lt_10k' | '10k_100k' | '100k_1m' | '1m_10m' | 'gt_10m';
export type LevBand = '0_2' | '2_5' | '5_10' | '10_plus';
export type AgeBand = 'h24' | 'd1_7' | 'd7_plus' | 'undated';
export type PnlState = 'profit' | 'underwater';

export interface MatrixFilters {
  win_rate_min?: number;
  consistent_only?: boolean;
  account_band?: AccountBand;
  active_within_days?: 7 | 30;
  lev_band?: LevBand;
  age_band?: AgeBand;
  pnl_state?: PnlState;
}

export interface AssetDetail {
  asset: string;
  computed_at: string | null;
  resolution: string;
  note?: string;
  filters: {
    top: number; include_mm: boolean; include_hedgers?: boolean; min_notional: number;
    mm_excluded: number; hedger_excluded?: number; mm_unknown_included: number;
    win_rate_min: number | null; consistent_only: boolean;
    account_band: string | null; active_within_days: number | null;
    lev_band: string | null; age_band: string | null; pnl_state: string | null;
    matrix_excluded: Record<string, number>;
  };
  low_sample: boolean;
  sample_wallets: number;
  positioning: {
    wallets_long: number; wallets_short: number;
    entities_long?: number; entities_short?: number;
    notional_long: number; notional_short: number; net_notional: number;
    avg_lev_long: number | null; avg_lev_short: number | null;
    upnl_long: number; upnl_short: number;
    cohort_oi: number; longs_in_profit_pct: number | null;
  } | null;
  entry_distribution: { long: EntryBucket[]; short: EntryBucket[]; mark: number | null };
  quality_weighting?: {
    weighted_wallets: number; excluded_wallets: number;
    quality_long: number; quality_short: number; beta: string;
  } | null;
  leverage_histogram: { long: LevBucket[]; short: LevBucket[] };
  age_buckets: { h24: number; d1_7: number; d7_plus: number; undated: number };
  liq_buckets: { below_mark: EntryBucket[]; above_mark: EntryBucket[] };
  crowding: { clusters: CrowdCluster[]; min_wallets: number; entry_tol_pct: number };
  trigger_clusters: TriggerClusters;
  venue: { oi_usd: number | null; funding_hourly: number | null; mark: number | null };
  trend_48h: { t: string; net_notional: number; wallets_long: number; wallets_short: number }[];
}

export interface FlowEvent {
  id: number;
  detected_at: string;
  wallet: string;
  asset: string;
  event_type: 'OPEN' | 'CLOSE' | 'INCREASE' | 'REDUCE' | 'FLIP';
  side: string;
  size_before: number | null;
  size_after: number | null;
  notional_delta: number | null;
  ref_px: number | null;
  ref_px_approx: boolean;
  resolution: '20m' | 'ws';
  wallet_rank: number | null;
  win_rate_7d: number | null;
  profit_factor?: number | null;
  conviction?: Conviction | null;
  display_name?: string | null;
  is_mm?: boolean;
  outsized?: boolean;
  merged_count?: number;
}

export interface FlowsResponse {
  events: FlowEvent[];
  page: number;
  limit: number;
  window: string;
  computed_at: string;
  resolution: string;
}

// ---- Phase 2: Smart Money Index + Market Pulse ----------------------------

export interface SmiComponents { c1: number; c2: number; c3: number; c4: number; c5: number }

// Tier-2 Part A: the server publishes study rows ONLY past the hard gate
// (n>=30 per bucket/horizon AND span>=21d) — below it the payload carries the
// collecting counters and no numbers.
export interface TrackRecordRow {
  kind: 'smi' | 'smi_change_24h';
  bucket: string;
  horizon: '4h' | '24h' | '72h';
  n: number;
  hit_n: number | null;
  hit_rate: number | null;
  mean_ret: number | null;
  median_ret: number | null;
}

export interface TrackRecord {
  published: boolean;
  collecting: boolean;
  n_observations: number;
  days: number;
  publish_min_n: number;
  publish_min_days: number;
  computed_at?: string;
  rows?: TrackRecordRow[];
}

export interface SmiDetail {
  asset: string;
  calibrating: boolean;
  history_hours: number;
  track_record: TrackRecord | null;
  weights: { skew: number; flow: number; breadth: number; leverage: number; divergence: number };
  latest: {
    cycle_ts: string;
    smi: number;
    components: SmiComponents;
    inputs: Record<string, number | null>;
  } | null;
  series: { t: string; smi: number }[];
  computed_at: string | null;
  resolution: string;
}

export interface PulseTile {
  asset: string;
  smi: number;
  smi_dev: number;
  smi_delta_24h: number | null;
  cohort_oi: number;
  net_notional: number;
  stance: 'net_long' | 'net_short' | 'balanced';
  flow_dir_24h: -1 | 0 | 1;
  net_flow_24h: number;
  wallets: number;
  low_sample: boolean;
}

export interface PulseEvent {
  detected_at: string;
  wallet: string;
  asset: string;
  event_type: string;
  side: string;
  notional_delta: number | null;
  ref_px: number | null;
  ref_px_approx: boolean;
  resolution: string;
  wallet_rank: number | null;
  display_name?: string | null;
  outsized?: boolean;
}

export interface Divergence {
  asset: string;
  kind: 'funding' | 'price';
  cohort_side: string;
  skew: number;
  funding_hourly?: number;
  price_change_24h?: number;
  label: string;
}

export interface PulseResponse {
  computed_at: string | null;
  resolution: string;
  cohort: CohortSummary;
  calibrating: boolean;
  history_hours: number;
  tiles: PulseTile[];
  movers: PulseTile[];
  risk_rotation: { majors: string[]; series: { t: string; majors_share: number | null }[]; now: number | null };
  biggest_event_today: PulseEvent | null;
  outsized_threshold_p95: number | null;
  highlights: PulseEvent[];
  divergences: Divergence[];
  crowded_trades?: {
    clusters: { asset: string; side: string; entry_lo: number; entry_hi: number;
                wallet_count: number; notional: number }[];
    min_wallets: number; entry_tol_pct: number;
  };
}

export const getPulse = () =>
  api.get<PulseResponse>('/api/analytics/pulse').then((r) => r.data);

// ---- Tier-2 Part B: copy-modal context (READ-ONLY display) ------------------

export interface ContextCrowd {
  side: 'long' | 'short';
  wallet_count: number;
  entry_lo: number;
  entry_hi: number;
  notional: number;
}

export interface AnalyticsContext {
  asset: string;
  available: boolean;
  computed_at: string | null;
  resolution: string;
  note?: string;
  stance?: 'net_long' | 'net_short' | 'balanced';
  low_sample?: boolean;
  wallets_long?: number;
  wallets_short?: number;
  notional_long?: number;
  notional_short?: number;
  smi?: number | null;
  smi_calibrating?: boolean;
  crowding?: { long: ContextCrowd | null; short: ContextCrowd | null } | null;
  funding_hourly?: number | null;
  perpl?: { market_id: number; symbol: string } | null;
  holders_total?: number;
  holders?: string[];
}

export const getAnalyticsContext = (asset: string) =>
  api.get<AnalyticsContext>(`/api/analytics/context/${asset}`).then((r) => r.data);

export const getSmi = (asset: string, hours = 168) =>
  api.get<SmiDetail>(`/api/analytics/smi/${asset}`, { params: { hours } }).then((r) => r.data);

export const getAnalyticsAssets = () =>
  api.get<AssetsResponse>('/api/analytics/assets').then((r) => r.data);

export const getAssetDetail = (asset: string,
  params: { top?: number; include_mm?: boolean; include_hedgers?: boolean;
    min_notional?: number; quality_weights?: boolean } & MatrixFilters) =>
  api.get<AssetDetail>(`/api/analytics/assets/${asset}`, { params }).then((r) => r.data);

export const getFlows = (params: {
  asset?: string; type?: string; window?: string; min_notional?: number;
  include_mm?: boolean; page?: number; limit?: number;
}) => api.get<FlowsResponse>('/api/analytics/flows', { params }).then((r) => r.data);

// ---- Phase 3.5: "Who moved" page (Design Guide B4) --------------------------

export interface MoverPerson {
  wallet: string;
  rank: number | null;
  display_name: string | null;
  win_rate_7d: number | null;
  trades_7d: number | null;
  profit_factor?: number | null;
  max_drawdown_7d?: number | null;
  max_drawdown_pct_7d?: number | null;
  flip_accuracy?: number | null;
  flip_n?: number | null;
  is_mm: boolean;
}

export interface MoverCard extends MoverPerson {
  asset: string;
  side: string;
  net_delta: number;
  kind: 'flipped' | 'opened' | 'closed' | 'grew' | 'cut';
  last_at: string | null;
  conviction: Conviction | null;
}

export interface ChurnRow extends MoverPerson {
  asset: string;
  side: string;
  notional: number;
  at: string;
}

export interface MoversResponse {
  window: string;
  asset: string | null;
  movers: MoverCard[];
  entrants: ChurnRow[];
  exits: ChurnRow[];
  cohort: CohortSummary;
  computed_at: string;
  resolution: string;
}

export const getMovers = (params: { window?: string; asset?: string }) =>
  api.get<MoversResponse>('/api/analytics/movers', { params }).then((r) => r.data);
