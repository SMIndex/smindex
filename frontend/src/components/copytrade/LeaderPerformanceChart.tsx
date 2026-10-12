import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { createChart, ColorType, AreaSeries } from 'lightweight-charts';
import LoadingSpinner from '@/components/common/LoadingSpinner';

interface Props {
  walletAddress: string;
}

export default function LeaderPerformanceChart({ walletAddress }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);
  const [days, setDays] = useState(30);

  const { data, isLoading } = useQuery({
    queryKey: ['leader-performance', walletAddress, days],
    queryFn: () => api.get(`/api/leaders/performance/${walletAddress}?days=${days}`).then((r) => r.data),
    enabled: !!walletAddress,
  });

  useEffect(() => {
    if (!chartRef.current || !data || data.length === 0) return;

    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 200,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(107, 114, 128, 0.1)' },
        horzLines: { color: 'rgba(107, 114, 128, 0.1)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });

    const lastPnl = data[data.length - 1]?.pnl_total ?? 0;
    const isPositive = lastPnl >= 0;

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: isPositive ? '#22c55e' : '#ef4444',
      topColor: isPositive ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)',
      bottomColor: isPositive ? 'rgba(34, 197, 94, 0.02)' : 'rgba(239, 68, 68, 0.02)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => `$${p.toFixed(0)}` },
    });

    const chartData = data.map((s: any) => ({
      time: Math.floor(new Date(s.timestamp).getTime() / 1000),
      value: s.pnl_total,
    }));

    areaSeries.setData(chartData);
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
  }, [data]);

  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-2 border-b border-text-secondary/10 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-text-primary">Performance</h3>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={clsx('text-[10px] px-2 py-0.5 rounded', days === d ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary hover:text-text-primary')}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-8"><LoadingSpinner /></div>
      ) : !data || data.length === 0 ? (
        <div className="text-center py-8 text-xs text-text-secondary">No performance data yet</div>
      ) : (
        <div ref={chartRef} className="w-full" />
      )}
    </div>
  );
}
