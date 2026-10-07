// Dynamic markets hook. Reads the live Perpl market registry via GET /api/markets.
// Use this for ACTIVE market selectors (copy setup, terminal) instead of any
// hardcoded BTC/MON/ETH/SOL list — if Perpl delists SOL or adds HYPE, this updates.
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getMarketRegistry, type RegistryMarket } from '@/lib/marketsApi';

export function useMarkets() {
  const query = useQuery({
    queryKey: ['market-registry'],
    queryFn: getMarketRegistry,
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
  });

  const markets = query.data?.markets ?? [];
  const activeMarkets = useMemo(() => markets.filter((m) => m.is_active), [markets]);
  const byId = useMemo(() => {
    const map = new Map<number, RegistryMarket>();
    for (const m of markets) map.set(m.market_id, m);
    return map;
  }, [markets]);
  const bySymbol = useMemo(() => {
    const map = new Map<string, RegistryMarket>();
    for (const m of markets) map.set(m.symbol.toUpperCase(), m);
    return map;
  }, [markets]);

  return {
    markets,
    activeMarkets,
    loading: query.isLoading,
    error: query.isError ? (query.error as any)?.message || 'Failed to load markets' : null,
    refetch: query.refetch,
    getById: (id: number) => byId.get(id),
    getBySymbol: (sym: string) => bySymbol.get((sym || '').toUpperCase()),
    fetchedAt: query.data?.fetched_at ?? null,
  };
}
