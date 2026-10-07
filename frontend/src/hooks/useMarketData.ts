import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useMarketStore } from '@/stores/marketStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { getMarkets } from '@/lib/api';
import type { MarketState } from '@/types/market';

export function useMarketData() {
  const { subscribe, unsubscribe } = useWebSocket();
  const markets = useMarketStore((s) => s.markets);
  const setMarkets = useMarketStore((s) => s.setMarkets);

  const { data, isLoading } = useQuery({
    queryKey: ['markets'],
    queryFn: getMarkets,
    refetchInterval: 60_000,
  });

  useEffect(() => {
    if (data) {
      const mapped: MarketState[] = data.map((m) => ({
        market_id: m.id,
        symbol: m.symbol,
        name: m.name,
        mark_price: m.mark_price,
        last_price: m.last_price,
        oracle_price: m.oracle_price,
        mid_price: m.mid_price,
        bid_price: m.bid_price,
        ask_price: m.ask_price,
        prev_price: m.prev_price,
        open_interest: m.open_interest,
        open_interest_usd: m.open_interest_usd,
        long_open_interest: (m.open_interest_usd || 0) / 2,
        short_open_interest: (m.open_interest_usd || 0) / 2,
        daily_volume: m.daily_volume,
        daily_volume_usd: m.daily_volume_usd,
        tvl: m.tvl,
        price_change_24h: m.price_change_24h,
        funding_rate: m.funding_rate,
        is_open: m.is_open,
        initial_margin: m.initial_margin,
        maintenance_margin: m.maintenance_margin,
        maker_fee: m.maker_fee,
        taker_fee: m.taker_fee,
      }));
      setMarkets(mapped);
    }
  }, [data, setMarkets]);

  useEffect(() => {
    subscribe('market_state');
    return () => unsubscribe('market_state');
  }, [subscribe, unsubscribe]);

  return { markets, isLoading };
}
