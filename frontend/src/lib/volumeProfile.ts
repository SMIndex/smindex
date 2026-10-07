/**
 * Volume Profile — aggregates candle volume by price bins.
 * Distributes each candle's volume uniformly across its [low, high] range.
 */

interface Candle {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface VolumeLevel {
  price: number;
  volume: number;
  buyVolume: number;
  sellVolume: number;
}

export function calculateVolumeProfile(
  candles: Candle[],
  numBins: number = 40,
): VolumeLevel[] {
  if (candles.length === 0) return [];

  // Find price range
  let minPrice = Infinity;
  let maxPrice = -Infinity;
  for (const c of candles) {
    if (c.low < minPrice) minPrice = c.low;
    if (c.high > maxPrice) maxPrice = c.high;
  }

  if (minPrice >= maxPrice) return [];

  const range = maxPrice - minPrice;
  const binWidth = range / numBins;

  // Initialize bins
  const bins: VolumeLevel[] = [];
  for (let i = 0; i < numBins; i++) {
    bins.push({
      price: minPrice + (i + 0.5) * binWidth,
      volume: 0,
      buyVolume: 0,
      sellVolume: 0,
    });
  }

  // Distribute volume
  for (const c of candles) {
    if (c.volume <= 0 || c.high <= c.low) continue;

    const isBuy = c.close >= c.open;
    const candleRange = c.high - c.low;

    for (let i = 0; i < numBins; i++) {
      const binLow = minPrice + i * binWidth;
      const binHigh = binLow + binWidth;

      // Overlap between candle range and bin
      const overlapLow = Math.max(c.low, binLow);
      const overlapHigh = Math.min(c.high, binHigh);

      if (overlapHigh > overlapLow) {
        const fraction = (overlapHigh - overlapLow) / candleRange;
        const vol = c.volume * fraction;
        bins[i].volume += vol;
        if (isBuy) bins[i].buyVolume += vol;
        else bins[i].sellVolume += vol;
      }
    }
  }

  return bins;
}
