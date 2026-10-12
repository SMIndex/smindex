/**
 * Chart pattern detection engine.
 * Runs client-side on real candle data from Perpl.
 * All algorithms use swing point detection on actual OHLC data.
 */

interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface SwingPoint {
  index: number;
  price: number;
  time: number;
  type: 'high' | 'low';
}

export interface DetectedPattern {
  type: string;
  direction: 'bullish' | 'bearish' | 'neutral';
  time: number;
  price: number;
  label: string;
}

// --- Swing Point Detection ---
function findSwingPoints(candles: Candle[], window: number = 5): SwingPoint[] {
  const points: SwingPoint[] = [];
  for (let i = window; i < candles.length - window; i++) {
    let isHigh = true;
    let isLow = true;
    for (let j = i - window; j <= i + window; j++) {
      if (j === i) continue;
      if (candles[j].high >= candles[i].high) isHigh = false;
      if (candles[j].low <= candles[i].low) isLow = false;
    }
    if (isHigh) points.push({ index: i, price: candles[i].high, time: candles[i].time, type: 'high' });
    if (isLow) points.push({ index: i, price: candles[i].low, time: candles[i].time, type: 'low' });
  }
  return points;
}

// --- Support / Resistance ---
function detectSupportResistance(candles: Candle[], swings: SwingPoint[]): DetectedPattern[] {
  if (swings.length < 3) return [];
  const patterns: DetectedPattern[] = [];
  const tolerance = 0.005; // 0.5%
  const used = new Set<number>();

  for (let i = 0; i < swings.length; i++) {
    if (used.has(i)) continue;
    let cluster = [swings[i]];
    for (let j = i + 1; j < swings.length; j++) {
      if (used.has(j)) continue;
      if (Math.abs(swings[j].price - swings[i].price) / swings[i].price < tolerance) {
        cluster.push(swings[j]);
        used.add(j);
      }
    }
    if (cluster.length >= 3) {
      const avgPrice = cluster.reduce((s, p) => s + p.price, 0) / cluster.length;
      const isResistance = cluster.filter((p) => p.type === 'high').length > cluster.length / 2;
      patterns.push({
        type: isResistance ? 'resistance' : 'support',
        direction: 'neutral',
        time: cluster[cluster.length - 1].time,
        price: avgPrice,
        label: isResistance ? 'R' : 'S',
      });
      used.add(i);
    }
  }
  return patterns;
}

// --- Double Top (bearish) ---
function detectDoubleTop(candles: Candle[], highs: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  const tolerance = 0.02; // 2%

  for (let i = 0; i < highs.length - 1; i++) {
    for (let j = i + 1; j < highs.length; j++) {
      if (j - i > 5) break; // max 5 swing points apart
      const priceDiff = Math.abs(highs[j].price - highs[i].price) / highs[i].price;
      if (priceDiff < tolerance && highs[j].index - highs[i].index >= 5) {
        // Check for trough between
        let minBetween = Infinity;
        for (let k = highs[i].index; k <= highs[j].index; k++) {
          minBetween = Math.min(minBetween, candles[k].low);
        }
        if (minBetween < highs[i].price * 0.97) {
          patterns.push({
            type: 'double_top',
            direction: 'bearish',
            time: highs[j].time,
            price: highs[j].price,
            label: 'Double Top',
          });
          break; // one per first peak
        }
      }
    }
  }
  return patterns;
}

// --- Double Bottom (bullish) ---
function detectDoubleBottom(candles: Candle[], lows: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  const tolerance = 0.02;

  for (let i = 0; i < lows.length - 1; i++) {
    for (let j = i + 1; j < lows.length; j++) {
      if (j - i > 5) break;
      const priceDiff = Math.abs(lows[j].price - lows[i].price) / lows[i].price;
      if (priceDiff < tolerance && lows[j].index - lows[i].index >= 5) {
        let maxBetween = -Infinity;
        for (let k = lows[i].index; k <= lows[j].index; k++) {
          maxBetween = Math.max(maxBetween, candles[k].high);
        }
        if (maxBetween > lows[i].price * 1.03) {
          patterns.push({
            type: 'double_bottom',
            direction: 'bullish',
            time: lows[j].time,
            price: lows[j].price,
            label: 'Double Bottom',
          });
          break;
        }
      }
    }
  }
  return patterns;
}

// --- Head & Shoulders (bearish) ---
function detectHeadAndShoulders(highs: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  const shoulderTol = 0.05; // 5%

  for (let i = 0; i < highs.length - 2; i++) {
    const left = highs[i];
    const head = highs[i + 1];
    const right = highs[i + 2];

    // Head must be highest
    if (head.price <= left.price || head.price <= right.price) continue;

    // Shoulders within tolerance
    const shoulderDiff = Math.abs(left.price - right.price) / left.price;
    if (shoulderDiff > shoulderTol) continue;

    // Head significantly above shoulders
    if (head.price < left.price * 1.02) continue;

    patterns.push({
      type: 'head_shoulders',
      direction: 'bearish',
      time: right.time,
      price: right.price,
      label: 'H&S',
    });
  }
  return patterns;
}

// --- Inverse Head & Shoulders (bullish) ---
function detectInverseHeadAndShoulders(lows: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  const shoulderTol = 0.05;

  for (let i = 0; i < lows.length - 2; i++) {
    const left = lows[i];
    const head = lows[i + 1];
    const right = lows[i + 2];

    if (head.price >= left.price || head.price >= right.price) continue;
    const shoulderDiff = Math.abs(left.price - right.price) / left.price;
    if (shoulderDiff > shoulderTol) continue;
    if (head.price > left.price * 0.98) continue;

    patterns.push({
      type: 'inv_head_shoulders',
      direction: 'bullish',
      time: right.time,
      price: right.price,
      label: 'Inv H&S',
    });
  }
  return patterns;
}

// --- Ascending Triangle (bullish) ---
function detectAscendingTriangle(candles: Candle[], highs: SwingPoint[], lows: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  if (highs.length < 2 || lows.length < 3) return patterns;

  // Flat resistance: recent highs within 1%
  const recentHighs = highs.slice(-4);
  const avgHigh = recentHighs.reduce((s, h) => s + h.price, 0) / recentHighs.length;
  const highFlat = recentHighs.every((h) => Math.abs(h.price - avgHigh) / avgHigh < 0.01);

  // Rising support: each low higher than previous
  const recentLows = lows.slice(-4);
  let rising = true;
  for (let i = 1; i < recentLows.length; i++) {
    if (recentLows[i].price <= recentLows[i - 1].price) { rising = false; break; }
  }

  if (highFlat && rising && recentHighs.length >= 2 && recentLows.length >= 3) {
    patterns.push({
      type: 'ascending_triangle',
      direction: 'bullish',
      time: recentHighs[recentHighs.length - 1].time,
      price: avgHigh,
      label: 'Asc Tri',
    });
  }

  return patterns;
}

// --- Descending Triangle (bearish) ---
function detectDescendingTriangle(candles: Candle[], highs: SwingPoint[], lows: SwingPoint[]): DetectedPattern[] {
  const patterns: DetectedPattern[] = [];
  if (lows.length < 2 || highs.length < 3) return patterns;

  const recentLows = lows.slice(-4);
  const avgLow = recentLows.reduce((s, l) => s + l.price, 0) / recentLows.length;
  const lowFlat = recentLows.every((l) => Math.abs(l.price - avgLow) / avgLow < 0.01);

  const recentHighs = highs.slice(-4);
  let falling = true;
  for (let i = 1; i < recentHighs.length; i++) {
    if (recentHighs[i].price >= recentHighs[i - 1].price) { falling = false; break; }
  }

  if (lowFlat && falling && recentLows.length >= 2 && recentHighs.length >= 3) {
    patterns.push({
      type: 'descending_triangle',
      direction: 'bearish',
      time: recentLows[recentLows.length - 1].time,
      price: avgLow,
      label: 'Desc Tri',
    });
  }

  return patterns;
}

// --- Main Detection ---
export function detectAllPatterns(candles: Candle[]): DetectedPattern[] {
  if (candles.length < 20) return [];

  const swings = findSwingPoints(candles, 5);
  const highs = swings.filter((s) => s.type === 'high');
  const lows = swings.filter((s) => s.type === 'low');

  const patterns: DetectedPattern[] = [
    ...detectSupportResistance(candles, swings),
    ...detectDoubleTop(candles, highs),
    ...detectDoubleBottom(candles, lows),
    ...detectHeadAndShoulders(highs),
    ...detectInverseHeadAndShoulders(lows),
    ...detectAscendingTriangle(candles, highs, lows),
    ...detectDescendingTriangle(candles, highs, lows),
  ];

  return patterns;
}
