import { useEffect, useRef, useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useMarketData } from '@/hooks/useMarketData';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { formatPrice, formatPercent, formatCompact } from '@/lib/formatters';
import { getCandles } from '@/lib/api';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';

function MiniChart({ marketId }: { marketId: number }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);

  const now = Date.now();
  const from = now - 24 * 60 * 60 * 1000; // 24h ago

  const { data: candleData } = useQuery({
    queryKey: ['mini-candles', marketId],
    queryFn: () => getCandles(marketId, 3600000, from, now),
    refetchInterval: 60000,
  });

  useEffect(() => {
    if (!chartRef.current || !candleData) return;

    const candles = candleData.candles || candleData;
    if (!Array.isArray(candles) || candles.length === 0) return;

    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 150,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontSize: 9,
      },
      grid: {
        vertLines: { color: 'rgba(107, 114, 128, 0.05)' },
        horzLines: { color: 'rgba(107, 114, 128, 0.05)' },
      },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderVisible: false, timeVisible: false, visible: false },
      crosshair: {
        horzLine: { visible: false },
        vertLine: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: 'rgb(var(--color-success))',
      downColor: 'rgb(var(--color-danger))',
      borderUpColor: 'rgb(var(--color-success))',
      borderDownColor: 'rgb(var(--color-danger))',
      wickUpColor: 'rgb(var(--color-success))',
      wickDownColor: 'rgb(var(--color-danger))',
    });

    const chartData = candles.map((c: any) => ({
      time: Math.floor((c.t || c.time || c[0]) / 1000) as any,
      open: c.o || c.open || c[1],
      high: c.h || c.high || c[2],
      low: c.l || c.low || c[3],
      close: c.c || c.close || c[4],
    }));

    series.setData(chartData);
    chart.timeScale().fitContent();
    chartInstanceRef.current = chart;

    const handleResize = () => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartInstanceRef.current = null;
    };
  }, [candleData]);

  return <div ref={chartRef} className="w-full h-[150px]" />;
}

export default function MarketsPage() {
  const { markets, isLoading } = useMarketData();
  const [sortBy, setSortBy] = useState<'default' | 'volume' | 'change' | 'oi' | 'funding'>('default');

  const sortedIds = useMemo(() => {
    if (sortBy === 'default') return MARKET_IDS;
    return [...MARKET_IDS].sort((a, b) => {
      const ma = markets[a] || {};
      const mb = markets[b] || {};
      if (sortBy === 'volume') return ((mb as any).daily_volume_usd || 0) - ((ma as any).daily_volume_usd || 0);
      if (sortBy === 'change') return Math.abs((mb as any).price_change_24h || 0) - Math.abs((ma as any).price_change_24h || 0);
      if (sortBy === 'oi') return ((mb as any).open_interest_usd || 0) - ((ma as any).open_interest_usd || 0);
      if (sortBy === 'funding') return Math.abs((mb as any).funding_rate || 0) - Math.abs((ma as any).funding_rate || 0);
      return 0;
    });
  }, [sortBy, markets]);

  if (isLoading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-text-primary">Market Overview</h1>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as any)}
          className="text-xs bg-bg-secondary border border-text-secondary/20 rounded-lg px-2 py-1.5 text-text-primary"
        >
          <option value="default">Default</option>
          <option value="volume">By Volume</option>
          <option value="change">By 24h Change</option>
          <option value="oi">By Open Interest</option>
          <option value="funding">By Funding Rate</option>
        </select>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {sortedIds.map((id) => {
          const cfg = MARKETS[id];
          const m = markets[id];
          if (!m) return null;

          return (
            <div key={id} className="card p-0 overflow-hidden">
              {/* Header */}
              <div className="px-4 py-3 border-b border-text-secondary/10 flex flex-wrap items-center justify-between gap-1">
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold text-text-primary">{cfg.symbol}</span>
                  <span className="text-xs text-text-secondary">{cfg.name}</span>
                </div>
                <div className="flex items-center gap-2 sm:gap-3">
                  <span className={clsx('text-base sm:text-lg font-bold', m.price_change_24h >= 0 ? 'text-success' : 'text-danger')}>
                    ${formatPrice(m.mark_price, cfg.decimals)}
                  </span>
                  <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded', m.price_change_24h >= 0 ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger')}>
                    {formatPercent(m.price_change_24h)}
                  </span>
                </div>
              </div>

              {/* Mini Chart */}
              <MiniChart marketId={id} />

              {/* Stats */}
              <div className="grid grid-cols-2 sm:grid-cols-4 border-t border-text-secondary/10 overflow-hidden">
                <div className="px-3 py-2.5 border-r border-text-secondary/5">
                  <div className="text-[9px] uppercase tracking-wider text-text-secondary">Volume 24h</div>
                  <div className="text-xs font-bold text-text-primary mt-0.5">{formatCompact(m.daily_volume_usd ?? 0)}</div>
                </div>
                <div className="px-3 py-2.5 border-r border-text-secondary/5">
                  <div className="text-[9px] uppercase tracking-wider text-text-secondary">Open Interest</div>
                  <div className="text-xs font-bold text-text-primary mt-0.5">{formatCompact(m.open_interest_usd ?? 0)}</div>
                </div>
                <div className="px-3 py-2.5 border-r border-text-secondary/5">
                  <div className="text-[9px] uppercase tracking-wider text-text-secondary">Funding Rate</div>
                  <div className={clsx('text-xs font-bold mt-0.5', m.funding_rate != null && m.funding_rate >= 0 ? 'text-danger' : 'text-success')}>
                    {m.funding_rate != null ? `${(m.funding_rate * 100).toFixed(4)}%` : '--'}
                  </div>
                </div>
                <div className="px-3 py-2.5">
                  <div className="text-[9px] uppercase tracking-wider text-text-secondary">Bid / Ask</div>
                  <div className="text-xs font-bold text-text-primary mt-0.5">
                    <span className="text-success">{formatPrice(m.bid_price, cfg.decimals)}</span>
                    <span className="text-text-secondary mx-0.5">/</span>
                    <span className="text-danger">{formatPrice(m.ask_price, cfg.decimals)}</span>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
