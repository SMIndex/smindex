import { formatPrice } from '@/lib/formatters';
import type { HeatmapData } from '@/types/heatmap';

interface PriceAxisProps {
  data: HeatmapData;
  decimals: number;
}

export default function PriceAxis({ data, decimals }: PriceAxisProps) {
  const { current_price } = data;
  const priceMin = current_price * 0.8;
  const priceMax = current_price * 1.2;
  const step = (priceMax - priceMin) / 6;

  const labels: number[] = [];
  for (let i = 0; i <= 6; i++) {
    labels.push(priceMin + step * i);
  }

  return (
    <div className="flex items-center justify-between px-[60px] pr-[20px] py-2 text-[10px] text-text-secondary font-mono">
      {labels.map((price, i) => (
        <span key={i}>${formatPrice(price, decimals)}</span>
      ))}
    </div>
  );
}
