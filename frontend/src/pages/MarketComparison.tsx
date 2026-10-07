import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createChart, ColorType, LineSeries } from 'lightweight-charts';
import { clsx } from 'clsx';
import { getCandles } from '@/lib/api';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { MARKET_CONFIGS, loadMarketConfigs } from '@/lib/perplTrading';
import { pearsonCorrelation, normalizeToPctChange } from '@/lib/correlation';
import { getChartColors, useTheme } from '@/hooks/useTheme';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const COLORS: Record<number, string> = { 1: '#f97316', 10: '#a855f7', 20: '#3b82f6' };
const TF_OPTIONS = [
  { label: '1h', resolution: 3600, range: 7 * 24 * 3600 * 1000 },
  { label: '4h', resolution: 14400, range: 30 * 24 * 3600 * 1000 },
  { label: '1d', resolution: 86400, range: 90 * 24 * 3600 * 1000 },
];

export default function MarketComparisonPage() {
  const chartRef = useRef<HTMLDivElement>(null);
  const [tf, setTf] = useState(TF_OPTIONS[0]);
  const { theme } = useTheme();

  useEffect(() => { loadMarketConfigs(); }, []);

  const now = Date.now();
  const from = now - tf.range;

  // Fetch candles for all markets
  const queries = MARKET_IDS.map((id) =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useQuery({
      queryKey: ['compare-candles', id, tf.resolution],
      queryFn: () => getCandles(id, tf.resolution, from, now),
      refetchInterval: 120000,
    }),
  );

  const allLoaded = queries.every((q) => q.data?.d?.length);
  const isLoading = queries.some((q) => q.isLoading);

  // Calculate correlations
  const closePrices: Record<number, number[]> = {};
  if (allLoaded) {
    MARKET_IDS.forEach((id, i) => {
      const mcfg = MARKET_CONFIGS[id];
      const pd = mcfg ? 10 ** mcfg.priceDecimals : 10;
      closePrices[id] = queries[i].data.d.map((c: any) => c.c / pd);
    });
  }

  const pctChanges: Record<number, number[]> = {};
  for (const id of MARKET_IDS) {
    if (closePrices[id]) pctChanges[id] = normalizeToPctChange(closePrices[id]);
  }

  // Correlation matrix
  const corr: Record<string, number> = {};
  for (let i = 0; i < MARKET_IDS.length; i++) {
    for (let j = i + 1; j < MARKET_IDS.length; j++) {
      const a = MARKET_IDS[i], b = MARKET_IDS[j];
      if (pctChanges[a] && pctChanges[b]) {
        corr[`${a}-${b}`] = pearsonCorrelation(pctChanges[a], pctChanges[b]);
      }
    }
  }

  // Render chart
  useEffect(() => {
    if (!chartRef.current || !allLoaded) return;

    const container = chartRef.current;
    container.innerHTML = '';

    const cc = getChartColors();
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 450,
      layout: { background: { type: ColorType.Solid, color: cc.bg }, textColor: cc.text },
      grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
      rightPriceScale: { borderColor: cc.border },
      timeScale: { borderColor: cc.border, timeVisible: true },
    });

    MARKET_IDS.forEach((id, i) => {
      const series = chart.addSeries(LineSeries, {
        color: COLORS[id] || '#ffffff',
        lineWidth: 2,
        priceFormat: { type: 'custom', formatter: (v: number) => `${v.toFixed(2)}%` },
      });

      const points = queries[i].data.d.map((c: any, idx: number) => ({
        time: Math.floor(c.t / 1000) as any,
        value: pctChanges[id]?.[idx] ?? 0,
      }));

      series.setData(points);
    });

    chart.timeScale().fitContent();
    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); };
  }, [allLoaded, tf, queries, theme]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-text-primary">Market Comparison</h1>
        <p className="text-xs text-text-secondary">Normalized % change overlay with correlation</p>
      </div>

      {/* Legend + Timeframe */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          {MARKET_IDS.map((id) => (
            <div key={id} className="flex items-center gap-1.5">
              <div className="w-3 h-1 rounded" style={{ backgroundColor: COLORS[id] }} />
              <span className="text-xs text-text-primary font-medium">{MARKETS[id].symbol}</span>
            </div>
          ))}
        </div>
        <div className="flex gap-1">
          {TF_OPTIONS.map((t) => (
            <button
              key={t.label}
              onClick={() => setTf(t)}
              className={clsx('px-3 py-1 rounded text-xs font-medium', tf.label === t.label ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary')}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
        ) : (
          <div ref={chartRef} className="w-full" />
        )}
      </div>

      {/* Correlation matrix */}
      {Object.keys(corr).length > 0 && (
        <div className="card">
          <h2 className="text-sm font-semibold text-text-primary mb-3">Correlation Matrix</h2>
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr>
                  <th className="px-3 py-2"></th>
                  {MARKET_IDS.map((id) => <th key={id} className="px-3 py-2 text-text-secondary">{MARKETS[id].symbol}</th>)}
                </tr>
              </thead>
              <tbody>
                {MARKET_IDS.map((a) => (
                  <tr key={a}>
                    <td className="px-3 py-2 font-medium text-text-primary">{MARKETS[a].symbol}</td>
                    {MARKET_IDS.map((b) => {
                      if (a === b) return <td key={b} className="px-3 py-2 text-center text-text-secondary">1.00</td>;
                      const key = a < b ? `${a}-${b}` : `${b}-${a}`;
                      const val = corr[key] ?? 0;
                      return (
                        <td key={b} className={clsx('px-3 py-2 text-center font-bold', val > 0.5 ? 'text-success' : val < -0.5 ? 'text-danger' : 'text-text-secondary')}>
                          {val.toFixed(3)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
