import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { clsx } from 'clsx';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { useHeatmapStore } from '@/stores/heatmapStore';
import { useLiquidationMap } from '@/hooks/useLiquidationMap';
import { useMarketStore } from '@/stores/marketStore';
import { formatPrice, formatCompact, formatPercent } from '@/lib/formatters';
import HeatmapCanvas from '@/components/heatmap/HeatmapCanvas';
import HeatmapLegend from '@/components/heatmap/HeatmapLegend';
import PriceAxis from '@/components/heatmap/PriceAxis';
import WhaleAlertFeed from '@/components/whales/WhaleAlertFeed';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const COIN_COLORS: Record<string, string> = {
  BTC: 'from-orange-500 to-orange-600',
  ETH: 'from-blue-500 to-blue-600',
  MON: 'from-purple-500 to-purple-600',
  SOL: 'from-emerald-500 to-emerald-600',
};

export default function HeatmapPage() {
  const { marketId: marketIdParam } = useParams();
  const selectedMarketId = useHeatmapStore((s) => s.selectedMarketId);
  const setSelectedMarket = useHeatmapStore((s) => s.setSelectedMarket);
  const markets = useMarketStore((s) => s.markets);

  useEffect(() => {
    if (marketIdParam) {
      const id = parseInt(marketIdParam, 10);
      if (id in MARKETS) setSelectedMarket(id);
    }
  }, [marketIdParam, setSelectedMarket]);

  const { heatmapData, isLoading } = useLiquidationMap(selectedMarketId);
  const marketConfig = MARKETS[selectedMarketId];
  const market = markets[selectedMarketId];

  const totalLongLiq = heatmapData?.bins?.reduce((s: number, b: any) => s + (b.long_liq_usd || 0), 0) ?? 0;
  const totalShortLiq = heatmapData?.bins?.reduce((s: number, b: any) => s + (b.short_liq_usd || 0), 0) ?? 0;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="relative overflow-hidden rounded-xl bg-gradient-to-br from-danger/10 via-bg-secondary to-bg-primary border border-danger/10 p-5">
        <div className="absolute top-0 right-0 w-48 h-48 bg-danger/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />
        <div className="relative flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-2xl font-bold text-text-primary tracking-tight">Liquidation Heatmap</h1>
            <p className="text-xs text-text-secondary mt-1">
              Visualize liquidation clusters and price magnets across markets
            </p>
          </div>
          {market && (
            <div className="text-right">
              <div className="text-2xl font-bold text-text-primary">
                ${formatPrice(market.mark_price, marketConfig?.decimals ?? 2)}
              </div>
              <span className={clsx('text-xs font-semibold', market.price_change_24h >= 0 ? 'text-success' : 'text-danger')}>
                {formatPercent(market.price_change_24h)}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Market tabs */}
      <div className="flex items-center gap-2">
        {MARKET_IDS.map((id) => {
          const config = MARKETS[id];
          const m = markets[id];
          const gradient = COIN_COLORS[config.symbol] || 'from-accent to-accent';
          return (
            <button
              key={id}
              onClick={() => setSelectedMarket(id)}
              className={clsx(
                'flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all',
                selectedMarketId === id
                  ? `bg-gradient-to-r ${gradient} text-white shadow-lg shadow-black/20`
                  : 'bg-bg-secondary text-text-secondary hover:text-text-primary hover:bg-bg-card border border-text-secondary/10',
              )}
            >
              <span className="font-bold">{config.symbol}</span>
              {m && (
                <span className={clsx('text-xs', selectedMarketId === id ? 'text-white/80' : 'text-text-secondary')}>
                  ${formatPrice(m.mark_price, config.decimals)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Stats row */}
      {heatmapData && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-gradient-to-br from-danger/10 to-bg-card border border-danger/10 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-danger" />
              <span className="text-[10px] uppercase tracking-wider text-text-secondary">Long Liq Volume</span>
            </div>
            <div className="text-xl font-bold text-danger">{formatCompact(totalLongLiq)}</div>
          </div>
          <div className="bg-gradient-to-br from-success/10 to-bg-card border border-success/10 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-success" />
              <span className="text-[10px] uppercase tracking-wider text-text-secondary">Short Liq Volume</span>
            </div>
            <div className="text-xl font-bold text-success">{formatCompact(totalShortLiq)}</div>
          </div>
          <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl p-4">
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Price Range</div>
            <div className="text-lg font-bold text-text-primary">
              {formatCompact(heatmapData.current_price * 0.8)} - {formatCompact(heatmapData.current_price * 1.2)}
            </div>
          </div>
          <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl p-4">
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Leverage Tiers</div>
            <div className="text-lg font-bold text-text-primary">2x - 100x</div>
          </div>
        </div>
      )}

      {/* Heatmap chart */}
      <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center h-[400px]">
            <LoadingSpinner size="lg" />
          </div>
        ) : heatmapData ? (
          <div>
            <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
                <span className="text-xs font-medium text-text-secondary">{marketConfig?.symbol} Liquidation Map</span>
              </div>
              <span className="text-[10px] text-text-secondary">Updates every 10s</span>
            </div>
            <HeatmapCanvas data={heatmapData} decimals={marketConfig?.decimals ?? 2} />
            <PriceAxis data={heatmapData} decimals={marketConfig?.decimals ?? 2} />
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-[400px] text-text-secondary">
            <svg className="w-12 h-12 mb-3 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            <p className="text-sm">No heatmap data for {marketConfig?.symbol ?? 'this market'}</p>
            <p className="text-[10px] mt-1">Data generates automatically every 10 seconds</p>
          </div>
        )}
      </div>

      {/* Info + Legend row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* How to read */}
        <div className="bg-gradient-to-br from-accent/5 to-bg-card border border-accent/10 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="p-1.5 rounded-lg bg-accent/10">
              <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <span className="text-sm font-semibold text-text-primary">How to read</span>
          </div>
          <div className="text-xs text-text-secondary leading-relaxed space-y-2">
            <p>
              <span className="inline-block w-2 h-2 rounded-full bg-danger mr-1 align-middle" />
              <span className="text-danger font-medium">Red bars</span> = long liquidation clusters (price drops trigger these)
            </p>
            <p>
              <span className="inline-block w-2 h-2 rounded-full bg-success mr-1 align-middle" />
              <span className="text-success font-medium">Green bars</span> = short liquidation clusters (price rises trigger these)
            </p>
            <p>
              <span className="inline-block w-2 h-2 rounded-full bg-accent mr-1 align-middle" />
              <span className="text-accent font-medium">Blue dashed line</span> = current market price
            </p>
            <p>Taller bars = more liquidation volume = stronger price magnet</p>
          </div>
        </div>

        <HeatmapLegend />
      </div>

      {/* Whale alerts */}
      <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-text-secondary/10">
          <div className="p-1.5 rounded-lg bg-warning/10">
            <svg className="w-4 h-4 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
          </div>
          <h2 className="text-sm font-semibold text-text-primary">Whale Activity — {marketConfig?.symbol}</h2>
        </div>
        <div className="p-4">
          <WhaleAlertFeed marketId={selectedMarketId} />
        </div>
      </div>
    </div>
  );
}
