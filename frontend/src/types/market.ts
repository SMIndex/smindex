export interface MarketState {
  market_id: number;
  symbol: string;
  name?: string;
  mark_price: number;
  last_price: number;
  bid_price: number;
  ask_price: number;
  prev_price: number;
  oracle_price?: number;
  mid_price?: number;
  open_interest: number;
  open_interest_usd?: number;
  long_open_interest?: number;
  short_open_interest?: number;
  daily_volume: number;
  daily_volume_usd?: number;
  tvl?: number;
  price_change_24h: number;
  funding_rate?: number;
  initial_margin?: number;
  maintenance_margin?: number;
  maker_fee?: number;
  taker_fee?: number;
  is_open?: boolean;
}

/** Shape returned by GET /api/markets (uses `id` instead of `market_id`) */
export interface MarketREST {
  id: number;
  symbol: string;
  name: string;
  mark_price: number;
  last_price: number;
  oracle_price?: number;
  mid_price?: number;
  bid_price: number;
  ask_price: number;
  prev_price: number;
  open_interest: number;
  open_interest_usd: number;
  daily_volume: number;
  daily_volume_usd: number;
  tvl: number;
  price_change_24h: number;
  is_open: boolean;
  initial_margin: number;
  maintenance_margin: number;
  maker_fee: number;
  taker_fee: number;
  funding_rate: number;
}
