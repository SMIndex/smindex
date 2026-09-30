// Strategies API client. Talks to /api/strategies/*.
// Route ids are '01'..'06' + '05c' (chase comparison) + '06u'; the backend strategy_id is 's01_liq_sweep' etc.
// Heuristic-mind models (docs 10–16) route as 'M1'..'M6'; backend ids 'm1_sweep_reclaim' etc. (config/models.yaml).
import api from '@/lib/api';
import type {
  StrategyState, RiskState, StrategyDetail, Signal, SignalRow, Trade, Parameter,
  ChangelogEntry, FeedHealth, MindBreakdown, AlertPref,
} from '@/types/strategies';

const SID_BY_NUM: Record<string, string> = {
  '01': 's01_liq_sweep', '02': 's02_funding_flow', '03': 's03_whale_follow',
  '04': 's04_vol_compression', '05': 's05_session_open', '06': 's06_hull_fisher_ema',
  '06u': 's06u_hull_fisher_ema', '05c': 's05c_session_open',
  'M1': 'm1_sweep_reclaim', 'M2': 'm2_bos_order_block', 'M3': 'm3_failed_auction',
  'M4': 'm4_htf_choch', 'M5': 'm5_session_liquidity_run', 'M6': 'm6_weekly_open_reclaim',
};

export const sidFromNum = (n: string): string => SID_BY_NUM[n] ?? n;
export const numFromSid = (sid: string): string =>
  (/^m\d_/.test(sid) ? `M${sid[1]}` : sid.startsWith('s06u') ? '06u' : sid.startsWith('s05c') ? '05c' : sid.slice(1, 3));
export const isModelNum = (n: string): boolean => /^M\d$/.test(n);
export const EVIDENCE_ORDER = ['05', '05c', '01', '04', '03', '02', '06', '06u', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6'];  // spec §3 default sort; 05c beside 05; models after

export const getRecentSignals = (hours = 24, limit = 300): Promise<SignalRow[]> =>
  api.get('/api/strategies/signals/recent', { params: { hours, limit } }).then((r) => r.data);

export const getOpenTrades = (): Promise<Trade[]> =>
  api.get('/api/strategies/trades/open').then((r) => r.data);

export const getStrategies = (): Promise<StrategyState[]> =>
  api.get('/api/strategies').then((r) => r.data);

export const getRisk = (): Promise<RiskState> =>
  api.get('/api/strategies/risk').then((r) => r.data);

export const getFeedHealth = (): Promise<FeedHealth[]> =>
  api.get('/api/strategies/feed-health').then((r) => r.data);

export const getStrategyDetail = (num: string): Promise<StrategyDetail> =>
  api.get(`/api/strategies/${sidFromNum(num)}`).then((r) => r.data);

export const getSignals = (num: string): Promise<Signal[]> =>
  api.get(`/api/strategies/${sidFromNum(num)}/signals`).then((r) => r.data);

export const getTrades = (num: string): Promise<Trade[]> =>
  api.get(`/api/strategies/${sidFromNum(num)}/trades`).then((r) => r.data);

export const getParameters = (num: string): Promise<Parameter[]> =>
  api.get(`/api/strategies/${sidFromNum(num)}/parameters`).then((r) => r.data);

export const getChangelog = (num: string): Promise<ChangelogEntry[]> =>
  api.get(`/api/strategies/${sidFromNum(num)}/changelog`).then((r) => r.data);

export const runBacktest = (num: string, refresh = false): Promise<{ strategy: string; days_back: number; report: string; cached: boolean; generated_ts: number | null }> =>
  api.get(`/api/strategies/${sidFromNum(num)}/backtest`, { params: refresh ? { refresh: true } : {}, timeout: 600_000 }).then((r) => r.data);

export const getBreakdown = (num: string): Promise<MindBreakdown> =>
  api.get(`/api/strategies/${sidFromNum(num)}/breakdown`).then((r) => r.data);

// Per-model Telegram switches for the signed-in wallet (doc 17 step 6). Absent row = enabled.
export const getAlertPrefs = (): Promise<AlertPref[]> =>
  api.get('/api/strategies/alerts/prefs').then((r) => r.data);

export const putAlertPrefs = (prefs: AlertPref[]): Promise<AlertPref[]> =>
  api.put('/api/strategies/alerts/prefs', { prefs }).then((r) => r.data);

export const patchMode = (num: string, requested_mode: string): Promise<StrategyState> =>
  api.patch(`/api/strategies/${sidFromNum(num)}/mode`, { requested_mode }).then((r) => r.data);

export const patchParameter = (num: string, key: string, value: string, reason: string): Promise<Parameter> =>
  api.patch(`/api/strategies/${sidFromNum(num)}/parameters`, { key, value, reason }).then((r) => r.data);

// Staleness is decided by the backend against per-source thresholds carried in
// the payload (Part A.1) — the UI never hardcodes a threshold. `stale` is the
// server flag; fall back to the payload threshold if only that is present; if
// neither is present (pre-restart of the API), treat as NOT stale (no false amber).
export function staleFeeds(feeds: FeedHealth[]): FeedHealth[] {
  return feeds.filter((f) => {
    if (typeof f.stale === 'boolean') return f.stale;
    if (f.stale_threshold_s != null && f.age_s != null) return f.age_s > f.stale_threshold_s;
    return false;
  });
}
