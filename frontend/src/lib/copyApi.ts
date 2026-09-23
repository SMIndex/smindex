// Copy Trading v1 API layer (PAPER-ONLY).
//
// Talks ONLY to the v1 backend routers:
//   /api/traders*      (discovery + profile, read-only)
//   /api/watchlist*    (read-only interest, separate from copy)
//   /api/copy/*        (paper subscriptions + dashboard reads)
//
// This module NEVER places a real order. It does not import perplTrading and is
// not used by the terminal OrderForm. Live copy stays gated by COPY_LIVE_ENABLED.
import api from '@/lib/api';

// --------------------------- types ---------------------------

export interface ActiveMarket {
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  size: number | string;
  leverage: number | string | null;
  entry_price: number | string | null;
  unrealized_pnl: number | string | null;
}

export interface ActivePositionSummary {
  active_positions_count: number | null;
  active_markets: ActiveMarket[];
  has_active_positions: boolean;
  active_positions_error?: boolean;
  active_positions_pending?: boolean;
}

export interface TraderListItem extends ActivePositionSummary {
  rank: number;
  wallet_address: string;
  exchange?: string;              // 'perpl' (default) | 'hl' 
  pnl_total: number;
  roi: number;
  volume: number;
  display_name: string | null;
  is_verified: boolean;
  source: string;
  last_fill_at?: string | null;   // ISO UTC of the most recent detected fill
  activity?: TraderActivity | null;
  fill_stats?: TraderFillStats | null;
}

// Tier-1 snapshot-derived activity metrics (hourly; NULL = not derivable)
export interface TraderActivity {
  active_days_7d: number | null;
  active_days_30d: number | null;
  last_active_at: string | null;      // ISO UTC
  vol_velocity_24h: number | null;    // USD traded over last ~24h
  consistency_flags: number | null;   // bit0 day, bit1 week, bit2 month, bit3 all
  pnl_trend_7d: number | null;        // USD/day slope of week PnL
  history_days: number | null;        // snapshot days available (honesty denominator)
  computed_at: string | null;
}

// Tier-2 fills-based quality stats (HL top-50 union; sampled hourly)
export interface TraderFillStats {
  trades_7d: number | null;
  win_rate_7d: number | null;         // 0..1; NULL when trades_7d < 10
  avg_hold_minutes: number | null;
  avg_trade_notional: number | null;
  maker_ratio: number | null;         // 0..1; NULL if venue flag absent
  realized_pnl_7d: number | null;
  sample_fills: number | null;
  sample_capped: boolean;             // page-capped fetch => labeled sample
  // Tier-2 Part D quality metrics (migration v13)
  profit_factor?: number | null;      // gross wins / losses; >=999 = no losses (UI: ∞)
  avg_win?: number | null;
  avg_loss?: number | null;
  avg_win_loss_ratio?: number | null;
  max_drawdown_7d?: number | null;    // USD, realized-equity path
  max_drawdown_pct_7d?: number | null;
  flip_accuracy?: number | null;      // NULL until price history covers flips
  flip_n?: number | null;
  computed_at: string | null;
}

export interface TraderProfile {
  wallet_address: string;
  display_name: string | null;
  is_verified: boolean;
  source: string;
  bio: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export interface TraderHeadlineStats {
  pnl_total: number | null;
  roi: number | null;
  volume: number | null;
  rank: number | null;
  on_leaderboard: boolean;
}

export interface TraderDetail {
  profile: TraderProfile;
  stats: TraderHeadlineStats;
  activity?: TraderActivity | null;
  fill_stats?: TraderFillStats | null;
}

export interface TraderDailyStat {
  trader_wallet: string;
  stat_date: string | null;
  pnl: number | null;
  roi: number | null;
  volume: number | null;
  win_rate: number | null;
  trades: number | null;
  max_drawdown: number | null;
  rank: number | null;
  captured_at: string | null;
}

export interface TraderOnChainPosition {
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entry_price: number;
  mark_price: number;
  pnl: number;
  deposit: number;
  leverage: number;
  notional: number;
  opened_at?: number | null;   // unix seconds (from on-chain entryBlock)
}

export interface WatchlistItem {
  id: number;
  trader_wallet: string;
  exchange?: string;              // 'perpl' (default) | 'hl'
  display_name: string | null;
  created_at: string | null;
}

export type CopyStatus = 'active' | 'paused' | 'stopped';

export interface CopySubscription {
  id: number;
  follower_wallet: string;
  trader_wallet: string;
  copy_type: string; // always 'paper' in v1
  mode: string;
  status: CopyStatus;
  sizing_mode: string;
  allocation_usd: number;
  max_leverage: number;
  max_margin_per_trade: number | null;
  max_daily_loss: number | null;
  max_total_loss: number | null;
  slippage_bps: number | null;
  allowed_markets: number[] | null;
  copy_new_only: boolean;
  sl_pct: number | null;
  tp_pct: number | null;
  live_enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export type CopyOrderStatus =
  | 'pending_confirmation' | 'submitted' | 'filled' | 'failed'
  | 'skipped' | 'risk_blocked' | 'simulated';

export interface CopyOrder {
  id: number;
  subscription_id: number | null;
  follower_wallet: string;
  trader_wallet: string;
  leader_event_id: number | null;
  mode: string; // paper | live_manual | live_auto
  market_id: number;
  symbol: string;
  side: string;
  intended_size: number | null;
  intended_price: number | null;
  leverage: number | null;
  allocation_usd: number | null;
  status: CopyOrderStatus | string;
  skip_reason: string | null;
  perpl_request_id: string | null;
  perpl_order_id: string | null;
  perpl_fill_id: string | null;
  fill_price: number | null;
  fill_size: number | null;
  submitted_at: string | null;
  filled_at: string | null;
  error_message: string | null;
  created_at: string | null;
}

export interface CreateCopyOrderPayload {
  trader_wallet: string;
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  intended_size: number;
  intended_price?: number | null;
  leverage: number;
  allocation_usd: number;
  subscription_id?: number | null;
  leader_event_id?: number | null;
  idempotency_key: string;
  action?: 'open' | 'close' | 'reduce';
  live_position_id?: number | null;
  order_type?: 'market' | 'limit';
  tp_price?: number | null;
  sl_price?: number | null;
  // audit A7: ad-hoc basis cap — omit = server default 30bps, 0 = cleared
  basis_cap_bps?: number | null;
}

// Live copy availability for THIS user (auth AND kill switch AND allowlist).
export interface LiveCopyStatus {
  authenticated: boolean;
  live_enabled: boolean;
  allowlisted: boolean;
  live_available: boolean;
}

// Execution-side numbers for the live copy modal. Nullable = feed unavailable
// (the modal shows em-dash and disables submit — fail closed).
export interface ExecutionContext {
  market_id: number;
  symbol: string | null;
  market_active: boolean;
  min_size: number | null;     // venue min posting size, base units (null = none)
  authenticated: boolean;
  mark_price: number | null;
  hl_mark: number | null;          // leader-venue (HL) live mid, same allMids ws feed
  funding_rate: number | null;
  basis_bps: number | null;
  max_leverage: number;
  account_id: number;
  available_balance: number | null;
  ts: string;
}

export const getLiveCopyStatus = () =>
  api.get<LiveCopyStatus>('/api/copy/live-status').then((r) => r.data);

export const getExecutionContext = (marketId: number) =>
  api.get<ExecutionContext>('/api/copy/execution-context', { params: { market_id: marketId } }).then((r) => r.data);

export interface LivePosition {
  id: number;
  copy_order_id: number | null;
  subscription_id: number | null;
  follower_wallet: string;
  trader_wallet: string;
  follower_market_id: number;
  triggers_failed?: boolean;   // audit A1: TP/SL placement failed — unprotected
  symbol: string;
  side: 'long' | 'short';
  status: 'open' | 'partially_closed' | 'closed' | 'failed' | 'orphaned';
  entry_price: number | null;
  entry_size: number | null;
  entry_margin: number | null;
  entry_leverage: number | null;
  current_size: number | null;
  close_price: number | null;
  realized_pnl: number | null;
  close_suggested: boolean;
  close_suggestion_type: 'close' | 'reduce' | null;
  suggested_at: string | null;
  opened_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
}

export interface UpdateCopyOrderPayload {
  status: 'filled' | 'placed' | 'failed';
  perpl_request_id?: string | null;
  perpl_order_id?: string | null;
  perpl_fill_id?: string | null;
  fill_price?: number | null;
  fill_size?: number | null;
  error_message?: string | null;
  tp_order_id?: string | null;
  sl_order_id?: string | null;
  triggers_failed?: boolean;   // audit A1
}

export interface PaperPosition {
  id: number;
  subscription_id: number;
  follower_wallet: string;
  trader_wallet: string;
  leader_event_id: number | null;
  mode: string;
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  entry_price: number | null;
  size: number | null;
  leverage: number | null;
  allocation_usd: number | null;
  status: 'open' | 'closed' | 'liquidated';
  close_price: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number;
  mark_price: number | null;
  close_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
}

export interface RiskEvent {
  id: number;
  subscription_id: number | null;
  follower_wallet: string;
  event_type: string;
  detail: Record<string, any> | null;
  created_at: string | null;
}

export interface AuditLog {
  id: number;
  actor_wallet: string;
  action: string;
  entity_type: string | null;
  entity_id: number | null;
  detail: Record<string, any> | null;
  created_at: string | null;
}

export interface CreateSubscriptionPayload {
  trader_wallet: string;
  exchange?: string;   // 'perpl' (default) | 'hl' — signal source only
  copy_type?: 'live_manual' | 'paper'; // default live_manual (server COPY_MODE)
  sizing_mode?: string; // 'fixed' for now
  allocation_usd: number;
  max_leverage: number;
  max_margin_per_trade?: number | null;
  max_daily_loss?: number | null;
  max_total_loss?: number | null;
  slippage_bps?: number | null;
  allowed_markets?: number[] | null;
  copy_new_only?: boolean;
  sl_pct?: number | null;
  tp_pct?: number | null;
  max_basis_bps?: number | null;   // HL-signal subs: block copies when |basis| exceeds
}

// --------------------------- traders (discovery + profile) ---------------------------

export const getTraders = (params?: {
  skip?: number;
  limit?: number;
  sort?: string;
  period?: string;
  exchange?: string;              // 'perpl' (default) | 'hl'
  wallets?: string;               // comma-separated: exact-address mode (analytics deep link)
}) => api.get<TraderListItem[]>('/api/traders', { params }).then((r) => r.data);

// Exchange registry — the Discover tabs render from this (adding an exchange
// is backend data, not new UI code).
export interface ExchangeInfo {
  id: string;
  label: string;
  periods: string[];
  has_live_positions: boolean;
  copy_execution: boolean;
}

export const getExchanges = () =>
  api.get<ExchangeInfo[]>('/api/exchanges').then((r) => r.data);

// Stable signature of the render-relevant fields of a trader. Used to preserve object
// identity across polls (so unchanged cards don't re-render) and as the React.memo key.
export function traderSignature(t: TraderListItem): string {
  return [
    t.rank, t.pnl_total, t.roi, t.volume, t.display_name, t.is_verified,
    t.active_positions_count, t.has_active_positions,
    t.active_positions_pending ? 1 : 0, t.active_positions_error ? 1 : 0,
    (t.active_markets || []).map((m) => `${m.market_id}:${m.side}`).join('|'),
    t.last_fill_at ?? '',
    t.activity?.computed_at ?? '',
    t.fill_stats?.computed_at ?? '',
  ].join(',');
}

export const getTrader = (wallet: string, exchange = 'perpl') =>
  api.get<TraderDetail>(`/api/traders/${wallet}`, { params: { exchange } }).then((r) => r.data);

export const getTraderStats = (wallet: string, days = 30) =>
  api.get<TraderDailyStat[]>(`/api/traders/${wallet}/stats`, { params: { days } }).then((r) => r.data);

// Real cumulative-PnL series (leaderboard_snapshots) covering the selected timeframe.
export type EquityTimeframe = '24h' | '7d' | '30d' | 'all';

// List/card sparklines: smaller point budget, batched in one request.
export const getEquityBatch = (wallets: string[], timeframe: EquityTimeframe = '7d', points = 60, exchange = 'perpl') =>
  api
    .get<Record<string, number[]>>('/api/traders/equity', { params: { wallets: wallets.join(','), timeframe, points, exchange } })
    .then((r) => r.data);

// Profile curve: richer series across the same timeframe.
export const getTraderEquity = (wallet: string, timeframe: EquityTimeframe = '7d', points = 160, exchange = 'perpl') =>
  api
    .get<{ wallet: string; timeframe: string; period: string; points: number[] }>(`/api/traders/${wallet}/equity`, { params: { timeframe, points, exchange } })
    .then((r) => r.data);

// Batch live active-position summaries keyed by lowercase wallet (for watchlist).
export const getActiveSummaries = (wallets: string[]) =>
  api
    .get<Record<string, ActivePositionSummary>>('/api/traders/active-summary', {
      params: { wallets: wallets.join(',') },
    })
    .then((r) => r.data);

export const getTraderPositions = (wallet: string) =>
  api
    .get<{
      wallet: string;
      positions: TraderOnChainPosition[];
      active_positions_count?: number | null;
      has_active_positions?: boolean;
      active_positions_error?: boolean;
    }>(`/api/traders/${wallet}/positions`)
    .then((r) => r.data);

// --------------------------- hyperliquid profile depth ---------------------------

export interface HlTpslOrder {
  coin: string;
  side: 'buy' | 'sell';
  limit_px: number;
  trigger_px: number | null;
  size: number;
  is_trigger: boolean;
  reduce_only: boolean;
  order_type: string | null;
  tpsl: boolean;
}

export interface HlPosition {
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
  perpl_market_id: number | null;   // null => "Not on Perpl"
  tpsl_orders: HlTpslOrder[];
  has_tpsl: boolean;
  sl_px: number | null;
  tp_px: number | null;
  rr: number | null;
  opened_at?: number | null;      // unix seconds (fill history, or funding ledger ±1h)
  opened_at_source?: 'fill' | 'fund_hr' | 'funding' | 'bound';  // dating confidence: EXACT | HOUR | DAY | open-before
  opened_at_pending?: boolean;    // background funding-ledger dating in progress
  opened_before?: number | null;  // fallback lower bound if dating fails
}

// Per-position build history (Tier-2 follow-up): derived server-side from the
// dating worker's single userFills fetch; the endpoint itself makes no venue
// calls. pending=true while the worker is still deriving.
export interface HlHistoryEntry {
  t: number;               // unix seconds UTC
  action: 'OPEN' | 'ADD' | 'REDUCE' | 'PARTIAL CLOSE' | 'FLIP' | 'CLOSE';
  size: number;
  px: number;
  run_size: number;
  run_avg: number | null;
  approx: boolean;         // truncated stream — running avg is ≈
  fills: number;           // raw fills merged into this entry
}

export interface HlPositionHistory {
  coin: string;
  side: 'long' | 'short';
  truncated: boolean;
  dated: { opened_at: number | null; opened_before: number | null; source: string | null } | null;
  entries: HlHistoryEntry[];
  elided: number;
  adds_count: number;
  first_fill_px: number;
  venue_entry: number | null;
  derived_entry: number | null;
  mismatch: boolean;       // derived != venue beyond rounding — venue is authoritative
}

export interface HlHistoryResponse {
  wallet: string;
  pending: boolean;
  fills_age_sec: number | null;
  histories: HlPositionHistory[];
}

export const getTraderHlHistory = (wallet: string) =>
  api.get<HlHistoryResponse>(`/api/traders/${wallet}/hl-history`).then((r) => r.data);

export interface HlProfileState {
  wallet: string;
  account_value: number;
  positions: HlPosition[];
  adds: HlTpslOrder[];
  pending_entries: HlTpslOrder[];
  copyable_pct: number | null;
  fetched_at: number;
}

export const getTraderHlState = (wallet: string) =>
  api.get<HlProfileState>(`/api/traders/${wallet}/hl-state`).then((r) => r.data);

// OPEN column for HL rows: batch open-position summaries (server shares the
// 300s hl profile cache; pending until primed — same contract as Perpl rows).
export interface HlActiveSummary {
  count: number | null;
  markets: ActiveMarket[];
  pending: boolean;
}

// Session cache: once a wallet's summary resolves (pending:false with a real
// count) it's served locally for the rest of the session — revisiting the tab
// makes zero new requests for those wallets. Failures (count:null) are NOT
// cached so a later visit can recover. Backend accepts max 25 wallets/request.
const hlActiveSession = new Map<string, HlActiveSummary>();

export async function getHlActiveBatch(wallets: string[]): Promise<Record<string, HlActiveSummary>> {
  const out: Record<string, HlActiveSummary> = {};
  const toFetch: string[] = [];
  for (const w of wallets) {
    const k = w.toLowerCase();
    const cached = hlActiveSession.get(k);
    if (cached) out[k] = cached;
    else toFetch.push(k);
  }
  for (let i = 0; i < toFetch.length; i += 25) {
    const chunk = toFetch.slice(i, i + 25);
    const data = await api
      .get<Record<string, HlActiveSummary>>('/api/traders/hl-active', { params: { wallets: chunk.join(',') } })
      .then((r) => r.data);
    for (const [k, s] of Object.entries(data)) {
      out[k] = s;
      if (!s.pending && s.count !== null) hlActiveSession.set(k, s);
    }
  }
  return out;
}

export interface HlBasisRow {
  coin: string;
  perpl_market_id: number;
  hl_mid: number | null;
  perpl_mark: number | null;
  basis_bps: number | null;
  hl_funding_hourly: number | null;
  perpl_funding: number | null;
}

export const getHlBasis = () =>
  api.get<HlBasisRow[]>('/api/hl/basis').then((r) => r.data);

// --------------------------- admin (server-enforced; UI affordance is cosmetic) ---------------------------

export const getAdminMe = () =>
  api.get<{ is_admin: boolean }>('/api/admin/me').then((r) => r.data);

export const adminHideTrader = (exchange: string, wallet: string) =>
  api.patch(`/api/admin/traders/${exchange}/${wallet}/hide`).then((r) => r.data);

export const adminUnhideTrader = (exchange: string, wallet: string) =>
  api.patch(`/api/admin/traders/${exchange}/${wallet}/unhide`).then((r) => r.data);

// --------------------------- watchlist (WATCH != COPY) ---------------------------

export const getWatchlist = () =>
  api.get<WatchlistItem[]>('/api/watchlist').then((r) => r.data);

export const addWatch = (traderWallet: string, exchange = 'perpl') =>
  api.post<WatchlistItem>(`/api/watchlist/${traderWallet}`, null, { params: { exchange } }).then((r) => r.data);

export const removeWatch = (traderWallet: string, exchange = 'perpl') =>
  api.delete<{ success: boolean }>(`/api/watchlist/${traderWallet}`, { params: { exchange } }).then((r) => r.data);

// --------------------------- copy subscriptions (paper) ---------------------------

export const getSubscriptions = () =>
  api.get<CopySubscription[]>('/api/copy/subscriptions').then((r) => r.data);

export const createSubscription = (payload: CreateSubscriptionPayload) =>
  api.post<CopySubscription>('/api/copy/subscriptions', payload).then((r) => r.data);

export const updateSubscription = (
  subId: number,
  fields: Partial<CreateSubscriptionPayload>,
) => api.patch<CopySubscription>(`/api/copy/subscriptions/${subId}`, fields).then((r) => r.data);

export const pauseSubscription = (subId: number) =>
  api.post<CopySubscription>(`/api/copy/subscriptions/${subId}/pause`).then((r) => r.data);

export const resumeSubscription = (subId: number) =>
  api.post<CopySubscription>(`/api/copy/subscriptions/${subId}/resume`).then((r) => r.data);

export const stopSubscription = (subId: number) =>
  api.post<CopySubscription>(`/api/copy/subscriptions/${subId}/stop`).then((r) => r.data);

// --------------------------- dashboard reads ---------------------------

export const getCopyOrders = (params?: {
  skip?: number;
  limit?: number;
  subscription_id?: number;
  trader_wallet?: string;
  status?: string;
  mode?: string; // 'live' = any live mode, or 'live_manual' | 'paper'
}) => api.get<CopyOrder[]>('/api/copy/orders', { params }).then((r) => r.data);

// Live copy order tracking (live_manual). createCopyOrder records the confirmed
// attempt + runs the risk/live gate; only place the real Perpl order when the
// returned status === 'submitted'. updateCopyOrder records the placement result.
export const createCopyOrder = (payload: CreateCopyOrderPayload) =>
  api.post<CopyOrder>('/api/copy/orders', payload).then((r) => r.data);

export const updateCopyOrder = (orderId: number, payload: UpdateCopyOrderPayload) =>
  api.patch<CopyOrder>(`/api/copy/orders/${orderId}`, payload).then((r) => r.data);

// Human-readable message for a blocked/skipped copy order (by backend skip_reason).
// Keep wallet_not_allowlisted exact: "Live copy beta access required".
export function copyOrderReasonLabel(skipReason?: string | null, status?: string): string {
  switch (skipReason) {
    case 'perpl_access_required': return 'Perpl trading access required';
    case 'wallet_not_perpl_approved': return 'Perpl trading access required';
    case 'wallet_not_allowlisted': return 'Live copy beta access required';
    case 'live_disabled': return 'Live copy is currently disabled';
    case 'live_mode_disabled': return 'Live manual copy is not the active mode';
    case 'market_inactive': return 'Market is no longer active on Perpl';
    case 'market_not_allowed': return 'Market not in your copy settings';
    case 'max_leverage_exceeded': return 'Leverage exceeds your copy limit';
    case 'max_margin_exceeded': return 'Margin exceeds your per-trade limit';
    case 'subscription_not_active': return 'This copy subscription is paused';
    case 'subscription_not_found': return 'No matching copy subscription';
    case 'leverage_out_of_range': return 'Leverage out of allowed range';
    case 'not_in_live_allowlist': return 'Live copy execution is in staged rollout — your wallet is not enabled yet';
    case 'basis_exceeded': return 'Cross-venue price gap exceeds your basis limit';
    case 'basis_unavailable': return 'Cross-venue basis unavailable — blocked (fail closed)';
    case 'invalid_trigger_prices': return 'TP/SL prices are on the wrong side of entry';
    case 'max_daily_loss_reached': return 'Daily loss limit reached for this subscription';
    case 'max_total_loss_reached': return 'Total loss limit reached for this subscription';
    case 'invalid_market': return 'Invalid market';
    case 'invalid_allocation': return 'Invalid allocation amount';
    default: return skipReason ? skipReason.replace(/_/g, ' ') : (status || 'Blocked');
  }
}

export const getCopyPositions = (params?: {
  status?: string;
  subscription_id?: number;
  trader_wallet?: string;
}) => api.get<PaperPosition[]>('/api/copy/positions', { params }).then((r) => r.data);

export const getLivePositions = (params?: { status?: string }) =>
  api.get<LivePosition[]>('/api/copy/live-positions', { params }).then((r) => r.data);

export const getRiskEvents = (params?: { skip?: number; limit?: number }) =>
  api.get<RiskEvent[]>('/api/copy/risk-events', { params }).then((r) => r.data);

export const getAuditLogs = (params?: { skip?: number; limit?: number }) =>
  api.get<AuditLog[]>('/api/copy/audit-logs', { params }).then((r) => r.data);

// Normalize any API error to a RENDERABLE string. FastAPI 422s carry `detail`
// as an ARRAY of error objects — rendering that as a React child crashed the
// whole route (found live: TP/SL request rejected with 422 -> blank page).
export function apiErrorMessage(e: any): string {
  const d = e?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map((x: any) => x?.msg
      ? `${(x.loc || []).filter((p: any) => p !== 'body').join('.')}: ${x.msg}`
      : JSON.stringify(x)).join('; ');
  }
  if (d != null) return JSON.stringify(d);
  return e?.message || 'Request failed';
}
