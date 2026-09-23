import { useRef, useEffect, useCallback } from 'react';
import type { HeatmapData } from '@/types/heatmap';
import { getChartColors, useTheme } from '@/hooks/useTheme';

interface HeatmapCanvasProps {
  data: HeatmapData;
  decimals: number;
}

function intensityToColor(intensity: number, isLong: boolean): string {
  const t = Math.min(Math.max(intensity, 0), 1);

  if (isLong) {
    // Long liquidations: dark red -> bright red -> orange
    const r = Math.round(80 + t * 175);
    const g = Math.round(20 + t * 80);
    const b = Math.round(20 + t * 20);
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    // Short liquidations: dark green -> bright green -> yellow-green
    const r = Math.round(20 + t * 100);
    const g = Math.round(80 + t * 175);
    const b = Math.round(20 + t * 20);
    return `rgb(${r}, ${g}, ${b})`;
  }
}

function drawHeatmap(
  ctx: CanvasRenderingContext2D,
  data: HeatmapData,
  width: number,
  height: number,
  decimals: number,
) {
  const { bins, current_price } = data;
  if (!bins || bins.length === 0) return;

  const cc = getChartColors();

  // Clear canvas
  ctx.fillStyle = cc.bg;
  ctx.fillRect(0, 0, width, height);

  const padding = { top: 20, bottom: 40, left: 60, right: 20 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // Calculate price range (current price +- 20%)
  const priceMin = current_price * 0.8;
  const priceMax = current_price * 1.2;

  // Find max intensity for normalization
  const maxIntensity = Math.max(...bins.map((b) => b.intensity), 0.001);

  // Map price to X coordinate
  const priceToX = (price: number) =>
    padding.left + ((price - priceMin) / (priceMax - priceMin)) * chartW;

  // Draw grid lines
  ctx.strokeStyle = '#21262d';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {
    const y = padding.top + (chartH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
  }

  // Draw bins
  const binWidth = Math.max(chartW / bins.length, 2);

  bins.forEach((bin) => {
    if (bin.price < priceMin || bin.price > priceMax) return;

    const x = priceToX(bin.price);
    const normalizedIntensity = bin.intensity / maxIntensity;

    // Draw long liquidation bar (pointing down from top)
    const longNorm = bin.long_liq_usd / Math.max(...bins.map((b) => b.long_liq_usd), 1);
    const longH = longNorm * (chartH / 2);
    if (longH > 0) {
      ctx.fillStyle = intensityToColor(normalizedIntensity, true);
      ctx.globalAlpha = 0.4 + normalizedIntensity * 0.6;
      ctx.fillRect(
        x - binWidth / 2,
        padding.top + chartH / 2 - longH,
        binWidth - 1,
        longH,
      );
    }

    // Draw short liquidation bar (pointing up from bottom)
    const shortNorm =
      bin.short_liq_usd / Math.max(...bins.map((b) => b.short_liq_usd), 1);
    const shortH = shortNorm * (chartH / 2);
    if (shortH > 0) {
      ctx.fillStyle = intensityToColor(normalizedIntensity, false);
      ctx.globalAlpha = 0.4 + normalizedIntensity * 0.6;
      ctx.fillRect(
        x - binWidth / 2,
        padding.top + chartH / 2,
        binWidth - 1,
        shortH,
      );
    }

    ctx.globalAlpha = 1;
  });

  // Draw center line (midpoint)
  ctx.strokeStyle = '#30363d';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(padding.left, padding.top + chartH / 2);
  ctx.lineTo(width - padding.right, padding.top + chartH / 2);
  ctx.stroke();
  ctx.setLineDash([]);

  // Draw current price vertical line
  const priceX = priceToX(current_price);
  ctx.strokeStyle = '#58a6ff';
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 3]);
  ctx.beginPath();
  ctx.moveTo(priceX, padding.top);
  ctx.lineTo(priceX, padding.top + chartH);
  ctx.stroke();
  ctx.setLineDash([]);

  // Price label
  ctx.fillStyle = '#58a6ff';
  ctx.font = '11px JetBrains Mono, monospace';
  ctx.textAlign = 'center';
  ctx.fillText(
    `$${current_price.toFixed(decimals)}`,
    priceX,
    padding.top - 6,
  );

  // Y-axis labels
  ctx.fillStyle = cc.text;
  ctx.font = '10px JetBrains Mono, monospace';
  ctx.textAlign = 'right';
  ctx.fillText('Long Liq', padding.left - 6, padding.top + 12);
  ctx.fillText('Short Liq', padding.left - 6, padding.top + chartH - 4);
}

export default function HeatmapCanvas({ data, decimals }: HeatmapCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = rect.width;
    const height = 400;

    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.scale(dpr, dpr);
    drawHeatmap(ctx, data, width, height, decimals);
  }, [data, decimals, theme]);

  useEffect(() => {
    draw();

    const handleResize = () => draw();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [draw]);

  return (
    <div ref={containerRef} className="w-full">
      <canvas ref={canvasRef} className="block" />
    </div>
  );
}
