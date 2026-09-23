import { create } from 'zustand';

export interface OrderBookLevel {
  price: number;
  size: number;
}

export interface RecentTrade {
  price: number;
  size: number;
  side: 'buy' | 'sell';
  time: number;
}

export interface ChartPattern {
  type: string;
  direction: 'bullish' | 'bearish' | 'neutral';
  time: number; // candle timestamp (seconds)
  price: number;
  label: string;
}

/** Order proposal staged from outside the trading UI (e.g. by the MCP server
 * for a Claude-driven trade). The Trade page parses ?stage=<base64> on mount,
 * decodes it into this shape, and stores it here for OrderForm to consume. */
export interface StagedOrder {
  marketId: number;
  symbol: string;
  side: 'long' | 'short';
  mode: 'market' | 'limit';
  amount: number;
  leverage: number;
  price: number | null;
  sl: number | null;
  tp: number | null;
  rationale: string | null;
}

interface TradingStoreState {
  selectedMarketId: number;
  resolution: number;
  orderBook: { bids: OrderBookLevel[]; asks: OrderBookLevel[] };
  recentTrades: RecentTrade[];
  patterns: ChartPattern[];
  limitPrice: number | null;
  stagedOrder: StagedOrder | null;

  setSelectedMarket: (id: number) => void;
  setResolution: (r: number) => void;
  setOrderBook: (bids: OrderBookLevel[], asks: OrderBookLevel[]) => void;
  addTrades: (trades: RecentTrade[]) => void;
  setPatterns: (p: ChartPattern[]) => void;
  setLimitPrice: (p: number | null) => void;
  setStagedOrder: (s: StagedOrder | null) => void;
}

export const useTradingStore = create<TradingStoreState>()((set) => ({
  selectedMarketId: 1,
  resolution: 900,
  orderBook: { bids: [], asks: [] },
  recentTrades: [],
  patterns: [],
  limitPrice: null,
  stagedOrder: null,

  setSelectedMarket: (id) => set({ selectedMarketId: id, orderBook: { bids: [], asks: [] }, recentTrades: [], patterns: [] }),
  setResolution: (r) => set({ resolution: r }),
  setOrderBook: (bids, asks) => set({ orderBook: { bids, asks } }),
  addTrades: (trades) => set((s) => ({
    recentTrades: [...trades, ...s.recentTrades].slice(0, 50),
  })),
  setPatterns: (p) => set({ patterns: p }),
  setLimitPrice: (p) => set({ limitPrice: p }),
  setStagedOrder: (s) => set({ stagedOrder: s }),
}));
