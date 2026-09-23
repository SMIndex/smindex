// Wallet Explorer API client (Part 2). Touches ONLY /api/explorer/*.
// Every payload carries fetched_at + weight_cost + provenance — render them.
// Every tab (fills/ledger/funding/orders/extras/history/activity) is PUBLIC
// since the density pass — this data is public on both venues. The tabs still
// lazy-load on first click, so the venue budget is unchanged.
import api from '@/lib/api';

export interface ExplorerResolution {
  role: 'user' | 'agent' | 'vault' | 'subAccount' | 'missing' | string;
  master: string | null;
  cached: boolean;
  checked_at: string | null;
}

export interface ExplorerPosition {
  coin: string;
  side: 'long' | 'short';
  size: number;
  entry_px: number;
  mark_px: number;
  leverage: number;
  liquidation_px: number | null;
  unrealized_pnl: number;
  margin_used: number;
  notional: number;
  perpl_market_id: number | null;
  roe: number | null;
  funding_since_open: number | null;
  margin_mode: string | null;
  opened_at?: number | null;
  opened_before?: number | null;
  opened_at_source?: string;
  opened_at_pending?: boolean;
  sl_px?: number | null;
  tp_px?: number | null;
  has_tpsl?: boolean;
  tpsl_orders?: ExplorerOrder[];
  rr?: number | null;
}

export interface ExplorerOrder {
  coin: string;
  side: string;
  limit_px: number;
  trigger_px: number | null;
  size: number;
  is_trigger: boolean;
  reduce_only: boolean;
  order_type: string | null;
  tpsl: boolean;
}

export interface EquitySeries { pnl: { t: number; v: number }[]; account_value: { t: number; v: number }[] }

export interface ExplorerTier1 {
  address: string;
  resolution: ExplorerResolution;
  hip3: string;
  provenance: Record<string, string>;
  vault?: { name: string | null; leader: string | null; apr: number | null; follower_count: number | null; is_closed: boolean | null } | null;
  hl_account: boolean;
  account_value?: number;
  withdrawable?: number | null;
  total_margin_used?: number | null;
  total_ntl_pos?: number | null;
  maintenance_margin?: number | null;
  positions?: ExplorerPosition[];
  adds?: ExplorerOrder[];
  pending_entries?: ExplorerOrder[];
  copyable_pct?: number | null;
  spot?: { balances: { coin: string; total: number; hold: number; entry_ntl: number | null }[]; usdc: number; note: string } | null;
  equity?: Record<string, EquitySeries> | null;
  equity_label?: string;
  cohort?: {
    ranks: Record<string, { rank: number; pnl: number; roi: number; volume: number }>;
    flags: { mm?: boolean | null; hedger?: boolean | null };
    fill_stats: { trades_7d: number; win_rate_7d: number | null; profit_factor: number | null; maker_ratio: number | null; realized_pnl_7d: number | null; max_drawdown_7d: number | null; sample_capped: boolean; computed_at: string | null } | null;
    sweep: { cycle_ts: string; account_value: number; gross_notional: number; n_assets: number } | null;
    in_cohort?: boolean;
    display_name?: string | null;
    source: string;
  };
  state_fetched_at?: number;
  fetched_at: number;
  weight_cost: number;
  requests: number;
}

export interface ExplorerFills {
  fills: { time: number; coin: string; dir: string; px: number; sz: number; closed_pnl: number | null; crossed: boolean | null; fee: number | null; fee_token: string | null }[];
  count: number; truncated: boolean; window_days: number; retention_note: string; fetched_at: number; weight_cost: number;
}

export interface ExplorerLedger {
  entries: { time: number; type: string; usdc: number; raw: Record<string, unknown> }[];
  count: number; truncated: boolean; net_deposits: number; deposits: number; withdrawals: number; window_days: number; note: string; fetched_at: number; weight_cost: number;
}

export interface ExplorerFunding {
  entries: { time: number; coin: string; usdc: number; szi: number | null; rate: number | null }[];
  count: number; truncated: boolean; total_by_coin: Record<string, number>; total: number; window_days: number; fetched_at: number; weight_cost: number;
}

export interface ExplorerOrdersHist {
  orders: { coin: string; side: string; limit_px: number | null; sz: number | null; orig_sz: number | null; order_type: string | null; reduce_only: boolean | null; is_trigger: boolean | null; trigger_px: number | null; timestamp: number | null; status: string | null; status_ts: number | null }[];
  count: number; note: string; fetched_at: number; weight_cost: number;
}

export interface ExplorerExtras {
  fees: { cross_rate: number | null; add_rate: number | null; daily_volume_14d: { date: string; exchange: number | null; user_cross: number | null; user_add: number | null }[] } | null;
  vault_equities: { vault: string; equity: number | null }[] | null;
  staking: { validator: string; amount: number | null; locked_until: number | null }[] | null;
  fetched_at: number; weight_cost: number;
}

export const getExplorerHl = (address: string) =>
  api.get<ExplorerTier1>(`/api/explorer/hl/${address}`).then((r) => r.data);
export const getExplorerFills = (address: string, days = 7) =>
  api.get<ExplorerFills>(`/api/explorer/hl/${address}/fills`, { params: { days } }).then((r) => r.data);
export const getExplorerLedger = (address: string, days = 90) =>
  api.get<ExplorerLedger>(`/api/explorer/hl/${address}/ledger`, { params: { days } }).then((r) => r.data);
export const getExplorerFunding = (address: string, days = 7) =>
  api.get<ExplorerFunding>(`/api/explorer/hl/${address}/funding`, { params: { days } }).then((r) => r.data);
export const getExplorerOrders = (address: string) =>
  api.get<ExplorerOrdersHist>(`/api/explorer/hl/${address}/orders`).then((r) => r.data);
export const getExplorerExtras = (address: string) =>
  api.get<ExplorerExtras>(`/api/explorer/hl/${address}/extras`).then((r) => r.data);


// ---- Perpl side (Part 3) ---------------------------------------------------

export interface PerplPosition {
  market_id: number; symbol: string; side: string; size: number;
  entry_price: number; mark_price: number; pnl: number; delta_pnl: number;
  funding_pnl: number; deposit: number; leverage: number; notional: number;
  liq_price: number | null; liq_source: string; collateral: number;
}

export interface PerplOrder {
  market_id: number; symbol: string; order_type: string; is_maker: boolean;
  is_taker: boolean; is_trigger: boolean; side: string; order_id: number;
  price: number | null; size: number | null; leverage: number | null;
  notional: number | null; margin_locked: number; expiry_block: number | null;
  kind: 'stop_loss' | 'take_profit' | 'limit';
}

export interface PerplLeaderboard {
  ranks: Record<string, { rank: number; pnl: number; roi: number; volume: number }>;
  on_leaderboard: boolean;
  snapshot_history: { t: string; rank: number; pnl: number }[];
  snapshot_appearances: number;
  snapshot_since: string | null;
  snapshot_note: string;
}

export interface ExplorerPerpl {
  address: string;
  history_note: string;
  provenance: Record<string, string>;
  perpl_account: boolean;
  account_id: number;
  balance?: number;
  margin_used?: number;
  positions?: PerplPosition[];
  orders?: PerplOrder[];
  liq_params?: Record<string, Record<string, number | boolean> | null>;
  leaderboard: PerplLeaderboard;
  fetched_at: number;
}

export const getExplorerPerpl = (address: string) =>
  api.get<ExplorerPerpl>(`/api/explorer/perpl/${address}`).then((r) => r.data);


// ---- Part 4: indexed Perpl history + cross-venue activity ------------------

export interface PerplHistEvent {
  at: string; block: number; tx: string; event_name: string; event_type: string;
  market_id: number | null; amount: number | null; balance_after: number | null;
  fee: number | null; bfa: number | null; attributed: boolean;
}

export interface ExplorerPerplHistory {
  address: string; events: PerplHistEvent[]; count: number;
  indexed_from: string | null;
  indexer: { last_block: number; head_block: number | null; first_event_block: number | null; backfill_pct: number | null; note: string | null; updated_at: string | null } | null;
  status_line: string | null;
  equity: { t: string; v: number }[];
  equity_label: string; provenance: string; fetched_at: number;
}

export interface ExplorerActivity {
  address: string;
  events: { venue: 'hl' | 'perpl'; at: string; type: string; amount: number | null; market_id?: number | null; fee?: number | null }[];
  count: number; window_days: number;
  hl_note: string | null; perpl_note: string | null;
  perpl_indexed_from: string | null; fetched_at: number;
}

export const getExplorerPerplHistory = (address: string, limit = 300) =>
  api.get<ExplorerPerplHistory>(`/api/explorer/perpl/${address}/history`, { params: { limit } }).then((r) => r.data);
export const getExplorerActivity = (address: string, days = 90) =>
  api.get<ExplorerActivity>(`/api/explorer/activity/${address}`, { params: { days } }).then((r) => r.data);

export const isEvmAddress = (s: string) => /^0x[0-9a-fA-F]{40}$/.test(s.trim());
export const normAddress = (s: string) => s.trim().toLowerCase();
