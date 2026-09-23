import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { createChart, ColorType, LineSeries } from 'lightweight-charts';
import { getChartColors, useTheme } from '@/hooks/useTheme';

const SIGNAL_LABELS: Record<string, { label: string; color: string }> = {
  strong_short: { label: 'STRONG SHORT', color: 'text-danger' },
  short_bias: { label: 'SHORT BIAS', color: 'text-danger/70' },
  neutral: { label: 'NEUTRAL', color: 'text-text-secondary' },
  long_bias: { label: 'LONG BIAS', color: 'text-success/70' },
  strong_long: { label: 'STRONG LONG', color: 'text-success' },
};

function FundingBar({ rate, maxRate }: { rate: number | null; maxRate: number }) {
  if (rate === null) return <span className="text-[10px] text-text-secondary/30">N/A</span>;
  const pct = maxRate > 0 ? Math.min(Math.abs(rate) / maxRate * 100, 100) : 0;
  const isPos = rate >= 0;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-[5px] bg-text-secondary/10 rounded-full overflow-hidden relative">
        <div
          className={clsx('absolute top-0 h-full rounded-full', isPos ? 'bg-danger/60 right-1/2' : 'bg-success/60 left-1/2')}
          style={{ width: `${pct / 2}%`, [isPos ? 'right' : 'left']: '50%' }}
        />
        <div className="absolute top-0 left-1/2 w-px h-full bg-text-secondary/20" />
      </div>
      <span className={clsx('text-[10px] font-bold tabular-nums', isPos ? 'text-danger' : 'text-success')}>
        {(rate * 100).toFixed(4)}%
      </span>
    </div>
  );
}

function HistoryChart({ symbol }: { symbol: string }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartObjRef = useRef<any>(null);
  const { theme } = useTheme();

  const { data } = useQuery({
    queryKey: ['funding-history-compare', symbol],
    queryFn: () => api.get(`/api/funding-compare/history/${symbol}?hours=72`).then((r) => r.data),
    refetchInterval: 300000,
  });

  useEffect(() => {
    if (!chartRef.current || !data) return;
    const container = chartRef.current;
    container.innerHTML = '';

    const cc = getChartColors();
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 120,
      layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text, fontSize: 9 },
      grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { mode: 0 },
    });
    chartObjRef.current = chart;

    // Hyperliquid line
    if (data.hyperliquid?.length) {
      const s = chart.addSeries(LineSeries, {
        color: '#6366f1', lineWidth: 1.5,
        priceFormat: { type: 'custom', formatter: (v: number) => `${(v * 100).toFixed(4)}%` },
        lastValueVisible: true, priceLineVisible: false,
      });
      s.setData(data.hyperliquid.map((p: any) => ({
        time: Math.floor(p.time / 1000) as any,
        value: p.funding_rate,
      })));
    }

    // Perpl line
    if (data.perpl?.length) {
      const s = chart.addSeries(LineSeries, {
        color: '#a78bfa', lineWidth: 1.5,
        priceFormat: { type: 'custom', formatter: (v: number) => `${(v * 100).toFixed(4)}%` },
        lastValueVisible: true, priceLineVisible: false,
      });
      s.setData(data.perpl.map((p: any) => ({
        time: Math.floor(new Date(p.timestamp).getTime() / 1000) as any,
        value: p.funding_rate,
      })));
    }

    // Zero line
    const firstSeries = chart.addSeries(LineSeries, {
      color: '#ffffff10', lineWidth: 1, lineStyle: 2,
      lastValueVisible: false, priceLineVisible: false,
    });
    if (data.hyperliquid?.length) {
      const times = data.hyperliquid.map((p: any) => Math.floor(p.time / 1000));
      firstSeries.setData([
        { time: times[0] as any, value: 0 },
        { time: times[times.length - 1] as any, value: 0 },
      ]);
    }

    chart.timeScale().fitContent();

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); chartObjRef.current = null; };
  }, [data]);

  // Theme update in-place — no chart rebuild
  useEffect(() => {
    if (!chartObjRef.current) return;
    const cc = getChartColors();
    chartObjRef.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text },
      grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
    });
  }, [theme]);

  return <div ref={chartRef} className="w-full" />;
}

export default function FundingComparison({ marketSymbol }: { marketSymbol: string }) {
  const [showChart, setShowChart] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ['funding-comparison'],
    queryFn: () => api.get('/api/funding-compare').then((r) => r.data),
    refetchInterval: 60000,
  });

  if (isLoading || !data) return null;

  // Find current market + compute max for bar scaling
  const allRates = data.flatMap((d: any) => [
    d.perpl?.funding_rate,
    d.hyperliquid?.funding_rate,
  ].filter((r: any) => r !== null && r !== undefined));
  const maxRate = Math.max(...allRates.map((r: number) => Math.abs(r)), 0.001);

  const current = data.find((d: any) => d.symbol === marketSymbol);

  return (
    <div className="border-t border-text-secondary/10">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-1.5 cursor-pointer hover:bg-bg-card/30 transition-colors"
        onClick={() => setShowChart(!showChart)}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Funding Rates</span>
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-accent/60" />
            <span className="text-[9px] text-accent/60">Perpl</span>
            <div className="w-2 h-2 rounded-full bg-indigo-500/60 ml-1" />
            <span className="text-[9px] text-indigo-400/60">Hyperliquid</span>
          </div>
        </div>
        {current && (
          <span className={clsx('text-[10px] font-bold', SIGNAL_LABELS[current.signal]?.color || 'text-text-secondary')}>
            {SIGNAL_LABELS[current.signal]?.label || 'NEUTRAL'}
          </span>
        )}
      </div>

      {/* Compact funding bars for all markets */}
      <div className="px-3 pb-2 grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1.5">
        {data.map((d: any) => (
          <div key={d.symbol} className={clsx('flex items-center gap-2', d.symbol === marketSymbol && 'opacity-100', d.symbol !== marketSymbol && 'opacity-50')}>
            <span className="text-[10px] font-bold text-text-primary w-7">{d.symbol}</span>
            <div className="flex-1 space-y-0.5">
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-accent/60 shrink-0" />
                <FundingBar rate={d.perpl?.funding_rate} maxRate={maxRate} />
              </div>
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-indigo-500/60 shrink-0" />
                <FundingBar rate={d.hyperliquid?.funding_rate} maxRate={maxRate} />
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Expandable history chart */}
      {showChart && (
        <div className="px-1">
          <div className="text-[9px] text-text-secondary/50 px-2 py-0.5 uppercase tracking-wider">72h Funding History — {marketSymbol}</div>
          <HistoryChart symbol={marketSymbol} />
        </div>
      )}
    </div>
  );
}
