import axios from 'axios';
import { API_BASE } from '@/config/constants';
import type { MarketREST } from '@/types/market';
import type { Leader, FollowConfig } from '@/types/copytrade';
import type { LeaderTrade } from '@/types/trade';
import type { HeatmapData } from '@/types/heatmap';
import type { WhaleAlert, OIDivergence } from '@/types/whale';

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Read auth token directly from localStorage — avoids circular dep with authStore
function getStoredToken(): string | null {
  try {
    const raw = localStorage.getItem('perpl-auth');
    if (raw) {
      const parsed = JSON.parse(raw);
      return parsed?.state?.token || null;
    }
  } catch {}
  return null;
}

api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      try { localStorage.removeItem('perpl-auth'); } catch {}
      window.dispatchEvent(new CustomEvent('auth_expired'));
    }
    return Promise.reject(error);
  },
);

// Market endpoints. /api/markets returns a registry envelope { markets, source,
// fetched_at, ttl_seconds }; unwrap to the market list for existing consumers.
export const getMarkets = () =>
  api.get<{ markets: MarketREST[] }>('/api/markets').then((r) => r.data.markets);

// Leader endpoints
export const getLeaders = (params?: {
  sort?: string;
  period?: string;
  active_only?: boolean;
  skip?: number;
  limit?: number;
}) => api.get<Leader[]>('/api/leaders', { params }).then((r) => r.data);

export const getLeader = (id: string) =>
  api.get<Leader>(`/api/leaders/${id}`).then((r) => r.data);

export const getLeaderTrades = (
  id: string,
  params?: { skip?: number; limit?: number },
) =>
  api
    .get<LeaderTrade[]>(`/api/leaders/${id}/trades`, { params })
    .then((r) => r.data);

// Trader positions (on-chain)
export const getTraderPositions = (walletAddress: string) =>
  api.get(`/api/leaders/positions/${walletAddress}`).then((r) => r.data);

// Order book (on-chain)
export const getOrderBook = (marketId: number) =>
  api.get(`/api/orderbook/${marketId}`).then((r) => r.data);

// Check if order forwarding is enabled for account
export const checkOrderForwarding = (walletAddress: string) =>
  api.get(`/api/check-forwarding/${walletAddress}`).then((r) => r.data);

// Order book whales (included in orderbook response)
export const getWhaleOrders = (marketId: number) =>
  api.get(`/api/orderbook/${marketId}`).then((r) => r.data.whales || []);

// Funding history
export const getFundingHistory = (marketId: number) =>
  api.get(`/api/funding-history/${marketId}`).then((r) => r.data);

// Block number
export const getCurrentBlock = () =>
  api.get('/api/block').then((r) => r.data.block as number);

// Candles
export const getCandles = (marketId: number, resolution: number, from: number, to: number) =>
  api.get(`/api/candles/${marketId}/${resolution}/${from}-${to}`).then((r) => r.data);

// Heatmap endpoints
export const getHeatmap = (marketId: number) =>
  api.get<HeatmapData>(`/api/heatmap/${marketId}`).then((r) => r.data);

// Whale endpoints
export const getWhaleAlerts = (params?: {
  market_id?: number;
  severity?: string;
  limit?: number;
}) => api.get<WhaleAlert[]>('/api/whales/alerts', { params }).then((r) => r.data);

export const getOIDivergence = (marketId: number) =>
  api
    .get<OIDivergence>(`/api/whales/oi-divergence/${marketId}`)
    .then((r) => r.data);

// Auth endpoints
export const postAuthPayload = (address: string) =>
  api
    .post<Record<string, string>>('/api/auth/payload', { address })
    .then((r) => r.data);

export const postAuthConnect = (message: string, signature: string) =>
  api
    .post<{ token: string; user: { wallet_address: string; perpl_linked: boolean } }>(
      '/api/auth/connect',
      { message, signature },
    )
    .then((r) => r.data);

export const postLinkPerpl = (perplToken: string) =>
  api.post('/api/auth/link-perpl', { perpl_token: perplToken }).then((r) => r.data);

export const postSetUsername = (username: string) =>
  api.post('/api/auth/set-username', { username }).then((r) => r.data);

export const getUsernames = (wallets: string[]) =>
  api.get<Record<string, string | null>>('/api/auth/usernames', { params: { wallets: wallets.join(',') } }).then((r) => r.data);

// Copy trade endpoints
export const postRegisterLeader = () =>
  api.post('/api/leaders/register').then((r) => r.data);

export const postFollow = (config: {
  leader_wallet: string;
  allocation_usd: number;
  max_leverage: number;
  auto_copy?: boolean;
}) => api.post('/api/copy/follow', config).then((r) => r.data);

export const deleteFollow = (leaderWallet: string) =>
  api.delete(`/api/copy/follow/${leaderWallet}`).then((r) => r.data);

export const patchFollow = (
  leaderWallet: string,
  update: { allocation_usd?: number; max_leverage?: number; auto_copy?: boolean; is_active?: boolean },
) =>
  api.patch(`/api/copy/follow/${leaderWallet}`, update).then((r) => r.data);

export const getMyFollows = () =>
  api.get<FollowConfig[]>('/api/copy/my-follows').then((r) => r.data);

export interface FollowedLeaderPosition {
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
}

export interface FollowedLeader {
  leader_wallet: string;
  leader_display_name: string | null;
  allocation_usd: number;
  max_leverage: number;
  auto_copy: boolean;
  is_active: boolean;
  positions: FollowedLeaderPosition[];
  error: string | null;
}

export const getFollowedLeadersPositions = (includePaused = false) =>
  api
    .get<FollowedLeader[]>('/api/copy/followed-leaders/positions', {
      params: { include_paused: includePaused },
    })
    .then((r) => r.data);

export interface AccountHealth {
  connected: boolean;
  message?: string;
  wallet_address?: string;
  account?: {
    equity: number;
    balance: number;
    available: number;
    margin_used: number;
    margin_ratio_pct: number;
    account_leverage: number;
    total_unrealized_pnl: number;
    total_notional: number;
    position_count: number;
  };
  risk?: any;
  positions?: any[];
}

export const getAccountHealth = () =>
  api.get<AccountHealth>('/api/account-health').then((r) => r.data);

// Positions
export const getMyPositions = () =>
  api.get('/api/positions/my').then((r) => r.data);

// Trade history
export const saveTrade = (trade: {
  market_id: number; symbol: string; side: string; action: string;
  order_type: string; size: number; price: number; leverage?: number;
  fee?: number; notional?: number; pnl?: number; order_id?: number;
  raw_response?: any; source?: string;
}) => api.post('/api/trades', trade).then((r) => r.data);

export const getTradeHistory = (params?: { skip?: number; limit?: number; market_id?: number }) =>
  api.get('/api/trades/history', { params }).then((r) => r.data);

export const saveOrder = (order: {
  market_id: number; symbol: string; direction: string; order_type: string;
  size: number; filled_size?: number; order_value?: number; price?: number;
  fill_price?: number; reduce_only?: boolean; status: string; order_id?: string;
  fee?: number; pnl?: number; source?: string; error?: string; raw_response?: any;
}) => api.post('/api/trades/orders', order).then((r) => r.data);

export const getOrderHistory = (params?: { skip?: number; limit?: number; market_id?: number }) =>
  api.get('/api/trades/orders', { params }).then((r) => r.data);

export const getPnlCard = () =>
  api.get('/api/trades/pnl-card').then((r) => r.data);

export const getCopyPerformance = () =>
  api.get('/api/trades/copy-performance').then((r) => r.data);

export default api;
