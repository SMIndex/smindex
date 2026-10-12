import { create } from 'zustand';
import type { HeatmapData } from '@/types/heatmap';

interface HeatmapStoreState {
  heatmaps: Record<number, HeatmapData>;
  selectedMarketId: number;
  setHeatmap: (marketId: number, data: HeatmapData) => void;
  setSelectedMarket: (id: number) => void;
}

export const useHeatmapStore = create<HeatmapStoreState>()((set) => ({
  heatmaps: {},
  selectedMarketId: 1,
  setHeatmap: (marketId, data) =>
    set((state) => ({
      heatmaps: {
        ...state.heatmaps,
        [marketId]: data,
      },
    })),
  setSelectedMarket: (id) => set({ selectedMarketId: id }),
}));
