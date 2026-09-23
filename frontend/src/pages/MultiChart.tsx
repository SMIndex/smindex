import { useEffect } from 'react';
import { useMarketData } from '@/hooks/useMarketData';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { loadMarketConfigs } from '@/lib/perplTrading';
import { formatPrice, formatPercent } from '@/lib/formatters';
import { clsx } from 'clsx';
import TradingChart from '@/components/terminal/TradingChart';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function MultiChartPage() {
  const { markets, isLoading } = useMarketData();

  useEffect(() => { loadMarketConfigs(); }, []);

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>;
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Multi-Chart</h1>
        <p className="text-xs text-text-secondary">All markets side by side with live data</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {MARKET_IDS.map((id) => {
          const config = MARKETS[id];
          const market = markets[id];
          return (
            <div key={id} className="card p-0 overflow-hidden">
              {/* Market header */}
              <div className="flex items-center justify-between px-3 py-2 border-b border-text-secondary/10">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-text-primary">{config.symbol}</span>
                  {market && (
                    <span className="text-sm text-text-primary">
                      ${formatPrice(market.mark_price, config.decimals)}
                    </span>
                  )}
                </div>
                {market && (
                  <span className={clsx(
                    'text-xs font-medium',
                    market.price_change_24h >= 0 ? 'text-success' : 'text-danger',
                  )}>
                    {formatPercent(market.price_change_24h)}
                  </span>
                )}
              </div>
              {/* Chart — compact mode, no pattern detection for performance */}
              <div style={{ height: 350 }}>
                <TradingChart marketId={id} symbol={config.symbol} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
