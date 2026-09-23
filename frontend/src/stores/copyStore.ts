import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface CopyRecord {
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  leverage: number;
  amount_usd: number;
  copied_from: string; // wallet address of trader
  timestamp: number;
}

interface CopyStoreState {
  copies: CopyRecord[];
  addCopy: (record: CopyRecord) => void;
  removeCopy: (market_id: number) => void;
  getCopySource: (market_id: number) => string | null;
  // Discover leaderboard sort ('pnl' | 'vol' | 'recent_activity'), persisted.
  discoverSort: string;
  setDiscoverSort: (sort: string) => void;
  // Last leverage the user PICKED in the live copy modal, per leader venue —
  // set only on slider interaction, used as the default on subsequent opens.
  lastLeverage: Record<string, number>;
  setLastLeverage: (venue: string, leverage: number) => void;
}

export const useCopyStore = create<CopyStoreState>()(
  persist(
    (set, get) => ({
      copies: [],
      discoverSort: 'pnl',
      setDiscoverSort: (sort) => set({ discoverSort: sort }),
      lastLeverage: {},
      setLastLeverage: (venue, leverage) =>
        set((state) => ({ lastLeverage: { ...state.lastLeverage, [venue]: leverage } })),
      addCopy: (record) =>
        set((state) => ({
          copies: [
            ...state.copies.filter((c) => c.market_id !== record.market_id),
            record,
          ],
        })),
      removeCopy: (market_id) =>
        set((state) => ({
          copies: state.copies.filter((c) => c.market_id !== market_id),
        })),
      getCopySource: (market_id) => {
        const copy = get().copies.find((c) => c.market_id === market_id);
        return copy?.copied_from ?? null;
      },
    }),
    { name: 'perpl-copy-trades' },
  ),
);
