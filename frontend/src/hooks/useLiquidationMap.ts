import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useHeatmapStore } from '@/stores/heatmapStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { getHeatmap } from '@/lib/api';

export function useLiquidationMap(marketId?: number) {
  const { subscribe, unsubscribe } = useWebSocket();
  const selectedMarketId = useHeatmapStore((s) => s.selectedMarketId);
  const heatmaps = useHeatmapStore((s) => s.heatmaps);
  const setHeatmap = useHeatmapStore((s) => s.setHeatmap);

  const activeMarketId = marketId ?? selectedMarketId;
  const heatmapData = heatmaps[activeMarketId] ?? null;

  const { data, isLoading } = useQuery({
    queryKey: ['heatmap', activeMarketId],
    queryFn: () => getHeatmap(activeMarketId),
    refetchInterval: 30_000,
  });

  useEffect(() => {
    if (data) {
      setHeatmap(activeMarketId, data);
    }
  }, [data, activeMarketId, setHeatmap]);

  useEffect(() => {
    subscribe('heatmap', { market_id: activeMarketId });
    return () => unsubscribe('heatmap');
  }, [activeMarketId, subscribe, unsubscribe]);

  return { heatmapData, isLoading };
}
