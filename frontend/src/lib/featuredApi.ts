import api from '@/lib/api';

// Featured traders API (/api/featured). Every data call can answer 403
// {reason:"whitelist"}; the page shows the gate then. Types mirror the
// backend rows (routers/featured.py), nothing is invented client side.

export type Venue = 'hl' | 'perpl';
export type Label = 'consistent' | 'asymmetric';

export interface FeaturedRow {
  rank: number;
  as_of: string | null;
  venue: Venue;
  wallet: string;
  display_name: string | null;
  label: Label | null;
  qualified: boolean;
  inactive: boolean;
  one_big_win: boolean;
  score: number | null;
  verdict: string | null;
  style: string | null;
  confidence: string | null;
  trades_90d: number;
  days_active_90d: number;
  win_rate_90d: number | null;
  win_rate_30d: number | null;
  net_pnl_90d: number | null;
  net_pnl_30d: number | null;
  net_pnl_all: number | null;
  profit_factor_90d: number | null;
  profit_factor_30d: number | null;
  avg_win_usd: number | null;
  avg_loss_usd: number | null;
  avg_win_pct_eq: number | null;
  avg_loss_pct_eq: number | null;
  avg_win_move_pct: number | null;
  avg_loss_move_pct: number | null;
  max_drawdown_90d: number | null;
  dd_start: string | null;
  dd_end: string | null;
  worst_trade_usd: number | null;
  worst_trade_pct_eq: number | null;
  worst_day_usd: number | null;
  worst_week_usd: number | null;
  largest_trade_share: number | null;
  profitable_weeks_13: number | null;
  longest_loss_streak: number | null;
  loss_streak_at: string | null;
  median_hold_sec: number | null;
  trades_per_week: number | null;
  trades_per_day_max: number | null;
  median_leverage: number | null;
  max_leverage: number | null;
  maker_share: number | null;
  pnl_per_volume: number | null;
  long_share: number | null;
  avg_equity_90d: number | null;
  account_value: number | null;
  volume_90d: number | null;
  top_coins: string[];
  top_coins_on_perpl: number;
  copiable_pct: number | null;
  liquidations_90d: number;
  usual_open: number | null;
  history_from: string | null;
  last_trade_at: string | null;
  last_trade_ms: number | null;
  open_count: number;
  unrealized_usd: number | null;
  positions_at: string | null;
  streak_now: string | null;
  averages_down_n: number | null;
  adds_to_winner_n: number | null;
  momentum: { win_rate: string | null; pf: string | null; pnl: string | null };
  checks_passed: number;
  active: boolean;
  open_positions?: OpenPosition[];
}

export interface OpenPosition {
  coin: string; side: 'long' | 'short'; size: number; size_usd: number; entry: number | null; mark: number | null;
  unrealized: number; leverage: number | null; liq_px: number | null; margin_usd: number | null; roe: number | null;
}

export interface Flag { pass: boolean | null; value: unknown }

export interface Replay {
  pnl: number; worst_dip: number; stopped_out: number; trades: number; wins: number; skipped: number; fees: number;
  margin_usd: number; leverage: number; stop_pct: number | null; curve: { t: number; v: number }[]; ms?: number;
  mae_from_candles?: number;
}

export interface ReplayDefaults {
  margin_usd: number; leverage: number; stop_pct: number | null; suggested_stop_pct: number | null;
  max_open: number | null; trader_median_leverage: number | null;
}

export interface ClosedTrade {
  id: number; coin: string; side: 'long' | 'short'; open_time: string; close_time: string; hold_sec: number;
  n_fills: number; n_adds: number; n_closes: number; maker_fills: number; max_size_usd: number; avg_entry: number;
  avg_exit: number; margin_usd: number | null; leverage: number | null; pnl_usd: number; pnl_pct_margin: number | null;
  ret_pct: number; fees: number; funding: number; entry_taker: boolean | null; averaged_down: boolean;
  added_to_winner: boolean; liquidation: boolean; mae_pct: number | null; mae_src: string | null;
}

export interface WalletDetail extends FeaturedRow {
  coin_stats: { coin: string; trades: number; wins: number; net: number; on_perpl: boolean; win_rate: number | null }[];
  hours_utc: number[];
  weekly: { start: number; end: number; pnl: number; n: number }[] | null;
  flags: Record<string, Flag>;
  score_inputs: Record<string, number | boolean>;
  equity_curve: { t: number; v: number; win: boolean; pnl: number; id: number }[];
  equity_points: [number, number][];
  replay: Replay;
  replay_defaults: ReplayDefaults;
  closed_trades: ClosedTrade[];
  closed_total: number;
  big_losses_year: ClosedTrade[];
  identity: { role: string | null; master: string | null; sub_accounts: string[] | null } | null;
  open_positions: OpenPosition[];
  server_time: string;
}

export interface TradeRow {
  id: number; venue: Venue; wallet: string; coin: string; side: 'long' | 'short'; label: Label | null;
  display_name: string | null; one_big_win: boolean; open_time: string; close_time: string; hold_sec: number;
  pnl_usd: number; pnl_pct_margin: number | null; ret_pct: number; avg_entry: number; avg_exit: number;
  n_fills: number; n_adds: number; n_closes: number; max_size_usd: number; leverage: number | null;
  entry_taker: boolean | null; averaged_down: boolean; added_to_winner: boolean; fees: number; funding: number;
  liquidation: boolean; entry_vs_24h_range: number | null; smi_at_entry: number | null; on_perpl?: boolean;
}

export interface TradeDetail extends TradeRow {
  margin_usd: number | null; pnl_gross: number | null; mae_pct: number | null; mfe_pct: number | null; mae_src: string | null;
  fills: { time: string; side: string; px: number; sz: number; fee: number; crossed: boolean | null; closed_pnl: number; dir: string; start_position: number; account: string }[];
  funding_rows: { time: string; usdc: number; rate: number | null }[];
  candles: { t: number; o: number; h: number; l: number; c: number }[];
  other_positions: { coin: string; side: string; open_time: string; close_time: string; pnl_usd: number }[];
}

export interface OpenNowCoin {
  coin: string; long: number; short: number; size_usd: number; avg_entry_long: number | null; avg_entry_short: number | null;
  on_perpl: boolean; smi: number | null;
  wallets: { venue: Venue; wallet: string; side: string; size_usd: number; entry: number | null; leverage: number | null; unrealized: number | null; label: Label | null }[];
}

export interface WalletsAnswer {
  rows: FeaturedRow[]; count: number; total: number; as_of: string | null; metrics_as_of: string | null; built_at: string; server_time: string;
}

export const isWhitelistError = (e: any) => e?.response?.status === 403 && (e?.response?.data?.reason === 'whitelist' || e?.response?.data?.detail === 'whitelist');

export const getWhitelistMe = () =>
  api.get<{ whitelisted: boolean; signed_in: boolean; admin: boolean; wallet?: string }>('/api/featured/whitelist/me').then((r) => r.data);

export const requestWhitelist = (body: { email: string; xHandle: string; wallet: string; hp?: string }) =>
  api.post<{ status: string }>('/api/featured/whitelist/request', body).then((r) => r.data);

export const getPreview = () => api.get('/api/featured/preview').then((r) => r.data);

export const getFeaturedWallets = (params: Record<string, string | number>) =>
  api.get<WalletsAnswer>('/api/featured/wallets', { params }).then((r) => r.data);

export const getFeaturedWallet = (venue: string, wallet: string, page = 1, size = 50) =>
  api.get<WalletDetail>(`/api/featured/wallets/${venue}/${wallet}`, { params: { page, size } }).then((r) => r.data);

export const getReplay = (venue: string, wallet: string, margin: number, lev: number, stopPct: number | null) =>
  api.get<Replay>(`/api/featured/wallets/${venue}/${wallet}/replay`, { params: { margin, lev, ...(stopPct ? { stopPct } : {}) } }).then((r) => r.data);

export const getFeaturedLive = (wallets: string[]) =>
  api.get<{ rows: Record<string, { last_trade_at: string | null; open_count: number; unrealized_usd: number | null; account_value: number | null; positions_at: string | null }> }>(
    '/api/featured/live', { params: { wallets: wallets.join(',') } }).then((r) => r.data);

export const getFeaturedTrades = (params: Record<string, string | number>) =>
  api.get<{ rows: TradeRow[]; next_cursor: number | null; count_window: number; wins_window: number; within: string }>('/api/featured/trades', { params }).then((r) => r.data);

export const getTradeDetail = (id: number) => api.get<TradeDetail>(`/api/featured/trades/${id}`).then((r) => r.data);

export const getOpenNow = () =>
  api.get<{ coins: OpenNowCoin[]; wallets: number; positions: number; as_of: string | null }>('/api/featured/open-now').then((r) => r.data);

// admin
export const adminWhitelist = (status?: string, q?: string) =>
  api.get('/api/admin/featured/whitelist', { params: { ...(status ? { status } : {}), ...(q ? { q } : {}) } }).then((r) => r.data);
export const adminDecide = (id: number, approve: boolean, note?: string) =>
  api.post(`/api/admin/featured/whitelist/${id}/${approve ? 'approve' : 'reject'}`, { note: note || null }).then((r) => r.data);
export const adminAddWallet = (wallet: string) => api.post('/api/admin/featured/whitelist/add', { wallet }).then((r) => r.data);
export const adminRemoveWallet = (wallet: string) => api.delete(`/api/admin/featured/whitelist/${wallet}`).then((r) => r.data);
export const adminFeaturedStatus = () => api.get('/api/admin/featured/status').then((r) => r.data);
export const adminRunFeatured = () => api.post('/api/admin/featured/run').then((r) => r.data);
