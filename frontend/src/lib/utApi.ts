import api from '@/lib/api';

// Unusual timing page: read-only /api/ut/* behind the Featured whitelist gate.

export type UtLevel = 'watch' | 'strong' | 'cluster';

export interface UtOpen { coin: string; side: 'long' | 'short'; size: number; notional: number; leverage: number | null; upnl: number | null; entry: number | null }

export interface UtRow {
  wallet: string; venue: 'hl' | 'perpl'; level: UtLevel; score: number; hits: number; entries: number;
  hit_rate: number | null; base_rate: number | null; p_value: number | null; odds_text: string;
  avg_move_2h: number | null; avg_move_24h: number | null; events_preceded: number; event_kinds: string[];
  misses: number; worst_loss_usd: number | null; age_days: number | null; age_known: boolean; ledger_checked: boolean;
  funder_label: string | null; funder: string | null; first_deposit_usd: number | null; coins: string[]; open: UtOpen | null;
  last_entry_at: string | null; cluster_id: number | null; cluster_size: number | null; moved_market_count: number;
  signals: Record<'s1' | 's2' | 's3' | 's4' | 's5' | 's6' | 's7', boolean>;
  top3_profit_share: number | null; median_hold_min: number | null; co_entries: number; exit_speed_hits: number; event_kinds_n: number;
  account_value: number | null;
  win_rate_90d: number | null; trips_90d: number | null; net_pnl_90d: number | null; pnl_total: number | null; pnl_total_from: string | null;
}

export interface UtEntry {
  id: number; time: string; coin: string; side: 'long' | 'short'; px: number | null; notional: number | null; leverage: number | null;
  ret_2h: number | null; ret_24h: number | null; mfe_2h: number | null; minutes_before_event: number | null; event_kind: string | null;
  close_after_peak_min: number | null; closed_at: string | null; pnl: number | null; hit: boolean | null; moved_market: boolean; is_add: boolean;
  size_vs_median: number | null;
}

export interface UtSignal { key: string; name: string; pass: boolean; value: string }

export interface UtCandles { coin: string; points: [number, number][]; events: { id: number; t: number; kind: string; direction: 'up' | 'down'; magnitude: number | null }[]; bars: number }

export interface UtWallet extends UtRow {
  entries_list?: UtEntry[]; entries: number; signals_detail: UtSignal[]; net_profit_90d: number | null; median_leverage: number | null;
  liquidations: number; trips: number; candles: UtCandles | null; level_words: string; scores_as_of: string | null;
  evidence_events: { id: number; kind: string; coin: string; time: string; direction: string; minutes_before: number | null; entry_coin: string; entry_side: string }[];
  top_trades: { coin: string; side: string; open: string; close: string; pnl_usd: number; size_usd: number }[];
}

export interface UtCluster {
  id: number; as_of: string; member_count: number; total_notional: number | null; lead_wallet: string; edge_kinds: string[];
  members: UtRow[]; member_wallets: string[];
  edges: { a: string; b: string; co_entries: number; on_events: number; shared_funder: boolean }[];
  co_entries: { time: string; coin: string; side: 'long' | 'short'; wallets_in: number; members: number; spread_s: number; notional: number;
    minutes_before: number | null; event: boolean; ret_2h: number | null; hit: boolean | null }[];
  events: { id: number; time: string; coin: string; kind: string; direction: string; magnitude: number | null; note: string | null }[];
  combined_profit_90d: number | null; combined_misses: number; combined_entries: number; combined_worst_loss_usd: number | null;
  same_way: { coin: string; side: string; count: number; notional: number } | null; scores_as_of: string | null;
}

export interface UtSummary {
  scores_as_of: string | null; scored: number; flagged: number; by_level: Record<UtLevel, number>; in_clusters: number;
  clusters: number; largest_cluster: number; open_now: { count: number; notional: number; short: number; long: number };
  last_alert: { wallet: string; coin: string; side: string; notional: number; level: string; leverage: number | null; at: string | null } | null;
  drift: { coin: string; score: number; funding_trend_48h: number | null; oi_change_6h: number | null; oi_change_24h: number | null; abn_ret_12h: number | null; flagged_entries_6h: number; at: string | null }[];
  next_event: { time: string; kind: string; coin: string; note: string | null } | null; live_at: string | null; server_time: string;
}

export interface UtWalletsAnswer { scores_as_of: string | null; scored: number; total: number; count: number; rows: UtRow[]; built_at: string }

export const getUtSummary = () => api.get<UtSummary>('/api/ut/summary').then((r) => r.data);
export const getUtWallets = (params: Record<string, string | number | boolean>) =>
  api.get<UtWalletsAnswer>('/api/ut/wallets', { params }).then((r) => r.data);
export const getUtWallet = (address: string) =>
  api.get<UtWallet & { entries: UtEntry[] }>(`/api/ut/wallet/${address}`).then((r) => r.data);
export const getUtCluster = (id: number) => api.get<UtCluster>(`/api/ut/cluster/${id}`).then((r) => r.data);
