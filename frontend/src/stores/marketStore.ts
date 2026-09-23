import { create } from 'zustand';
import type { MarketState } from '@/types/market';

interface MarketStoreState {
  markets: Record<number, MarketState>;
  updateMarket: (marketId: number, data: Partial<MarketState>) => void;
  setMarkets: (data: MarketState[]) => void;
}

export const useMarketStore = create<MarketStoreState>()((set) => ({
  markets: {},
  updateMarket: (marketId, data) =>
    set((state) => ({
      markets: {
        ...state.markets,
        [marketId]: {
          ...state.markets[marketId],
          ...data,
        } as MarketState,
      },
    })),
  // MERGE into existing entries (never replace wholesale): the REST re-seed
  // omits WS-only fields (oracle_price, mid_price) and must not wipe what the
  // push stream populated. Markets Perpl delists still disappear (only ids in
  // `data` survive).
  setMarkets: (data) =>
    set((state) => ({
      markets: Object.fromEntries(
        data.map((m) => [m.market_id, { ...state.markets[m.market_id], ...m }]),
      ),
    })),
}));
