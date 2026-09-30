import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createChart, ColorType, LineSeries, HistogramSeries } from 'lightweight-charts';
import { getFundingHistory } from '@/lib/api';
import { getChartColors, useTheme } from '@/hooks/useTheme';

interface FundingChartProps {
  marketId: number;
  symbol: string;
}

export default function FundingChart({ marketId, symbol }: FundingChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();

  const { data } = useQuery({
    queryKey: ['funding-history', marketId],
    queryFn: () => getFundingHistory(marketId),
    refetchInterval: 60000,
  });

  useEffect(() => {
    if (!chartRef.current || !data?.length) return;

    const container = chartRef.current;
    container.innerHTML = '';

    const cc = getChartColors();
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 120,
      layout: {
        background: { type: ColorType.Solid, color: cc.bg },
        textColor: cc.text,
        fontSize: 10,
      },
      grid: { vertLines: { color: cc.grid }, horzLines: { color: cc.grid } },
      rightPriceScale: { borderColor: cc.border },
      timeScale: { borderColor: cc.border, timeVisible: true },
    });

    const series = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'custom', formatter: (v: number) => `${(v * 100).toFixed(4)}%` },
    });

    const points = data
      .map((d: any) => ({
        time: Math.floor(d.timestamp) as any,
        value: d.funding_rate,
        color: d.funding_rate >= 0 ? 'rgba(248,81,73,0.6)' : 'rgba(63,185,80,0.6)',
      }))
      .sort((a: any, b: any) => a.time - b.time)
      // Deduplicate same timestamps
      .filter((p: any, i: number, arr: any[]) => i === 0 || p.time > arr[i - 1].time);

    series.setData(points);
    chart.timeScale().fitContent();

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener('resize', handleResize);
    return () => { window.removeEventListener('resize', handleResize); chart.remove(); };
  }, [data, theme]);

  return (
    <div>
      <div className="flex items-center justify-between px-3 py-1 border-t border-text-secondary/10">
        <span className="text-[10px] text-text-secondary">{symbol} Funding Rate History</span>
        <span className="text-[10px] text-text-secondary">Green = shorts pay, Red = longs pay</span>
      </div>
      <div ref={chartRef} className="w-full" />
      {(!data || data.length === 0) && (
        <div className="text-center py-4 text-[10px] text-text-secondary">Collecting funding rate data...</div>
      )}
    </div>
  );
}
