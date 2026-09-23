import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createChart, ColorType, CandlestickSeries } from 'lightweight-charts';
import { getCandles } from '@/lib/api';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { getChartColors, useTheme } from '@/hooks/useTheme';

interface PriceChartProps {
  marketId: number;
  symbol: string;
  entryPrice?: number;
  liqPrice?: number;
}

export default function PriceChart({ marketId, symbol, entryPrice, liqPrice }: PriceChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();
  const mcfg = MARKET_CONFIGS[marketId];
  const priceDec = mcfg?.priceDecimals ?? 1;

  const now = Math.floor(Date.now());
  const from = now - 24 * 60 * 60 * 1000; // 24h ago

  const { data } = useQuery({
    queryKey: ['candles', marketId],
    queryFn: () => getCandles(marketId, 900, from, now), // 15min candles
    refetchInterval: 60000,
  });

  useEffect(() => {
    if (!chartRef.current || !data?.d?.length) return;

    const container = chartRef.current;
    container.innerHTML = '';

    const cc = getChartColors();
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 300,
      layout: {
        background: { type: ColorType.Solid, color: cc.bg },
        textColor: cc.text,
      },
      grid: {
        vertLines: { color: cc.grid },
        horzLines: { color: cc.grid },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: cc.border,
      },
      timeScale: {
        borderColor: cc.border,
        timeVisible: true,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb950',
      downColor: '#f85149',
      borderDownColor: '#f85149',
      borderUpColor: '#3fb950',
      wickDownColor: '#f85149',
      wickUpColor: '#3fb950',
    });

    const pd = 10 ** priceDec;
    const candles = data.d.map((c: any) => ({
      time: Math.floor(c.t / 1000) as any,
      open: c.o / pd,
      high: c.h / pd,
      low: c.l / pd,
      close: c.c / pd,
    }));

    candleSeries.setData(candles);

    // Add entry price line
    if (entryPrice) {
      candleSeries.createPriceLine({
        price: entryPrice,
        color: '#58a6ff',
        lineWidth: 1,
        lineStyle: 2, // dashed
        axisLabelVisible: true,
        title: 'Entry',
      });
    }

    // Add liquidation price line
    if (liqPrice) {
      candleSeries.createPriceLine({
        price: liqPrice,
        color: '#f85149',
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: 'Liq',
      });
    }

    chart.timeScale().fitContent();

    const handleResize = () => {
      chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [data, entryPrice, liqPrice, priceDec, theme]);

  return (
    <div>
      <div className="flex items-center justify-between px-4 py-2 border-b border-text-secondary/10">
        <span className="text-xs font-medium text-text-secondary">{symbol} — 15min candles (24h)</span>
      </div>
      <div ref={chartRef} className="w-full" />
    </div>
  );
}
