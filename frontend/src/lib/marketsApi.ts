// Dynamic Perpl market registry (frontend). Source of truth for WHICH markets are
// active + their symbol/decimals. Backed by GET /api/markets (registry envelope),
// which the backend builds live from Perpl context. No hardcoded market list here.
import api from '@/lib/api';

export interface RegistryMarket {
  id?: number;
  market_id: number;
  symbol: string;
  name: string;
  price_decimals: number;
  size_decimals: number;
  is_active: boolean;
  // live state (present on /api/markets; absent on symbol lookup)
  mark_price?: number;
  daily_volume_usd?: number;
  price_change_24h?: number;
  maintenance_margin?: number;
  initial_margin?: number;
  [k: string]: any;
}

export interface MarketRegistryResponse {
  markets: RegistryMarket[];
  source: string;
  fetched_at: string | null;
  ttl_seconds: number;
}

export const getMarketRegistry = () =>
  api.get<MarketRegistryResponse>('/api/markets').then((r) => r.data);

export const getMarketBySymbol = (symbol: string) =>
  api.get<RegistryMarket>(`/api/markets/symbol/${symbol}`).then((r) => r.data);
