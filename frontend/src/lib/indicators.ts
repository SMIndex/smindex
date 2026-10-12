/**
 * Technical indicator calculations for chart overlays.
 * All functions take arrays of candle closes and return arrays of the same length
 * (padded with null for periods where there's not enough data).
 */

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface IndicatorPoint {
  time: number;
  value: number | null;
}

// --- Simple Moving Average ---
export function sma(candles: Candle[], period: number): IndicatorPoint[] {
  const result: IndicatorPoint[] = [];
  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      result.push({ time: candles[i].time, value: null });
    } else {
      let sum = 0;
      for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
      result.push({ time: candles[i].time, value: sum / period });
    }
  }
  return result;
}

// --- Exponential Moving Average ---
export function ema(candles: Candle[], period: number): IndicatorPoint[] {
  const result: IndicatorPoint[] = [];
  const k = 2 / (period + 1);

  for (let i = 0; i < candles.length; i++) {
    if (i < period - 1) {
      result.push({ time: candles[i].time, value: null });
    } else if (i === period - 1) {
      // First EMA = SMA of first `period` values
      let sum = 0;
      for (let j = 0; j < period; j++) sum += candles[j].close;
      result.push({ time: candles[i].time, value: sum / period });
    } else {
      const prev = result[i - 1].value!;
      result.push({ time: candles[i].time, value: candles[i].close * k + prev * (1 - k) });
    }
  }
  return result;
}

// --- Bollinger Bands ---
export interface BollingerPoint {
  time: number;
  upper: number | null;
  middle: number | null;
  lower: number | null;
}

export function bollingerBands(candles: Candle[], period: number = 20, stdDev: number = 2): BollingerPoint[] {
  const mid = sma(candles, period);
  const result: BollingerPoint[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (mid[i].value === null) {
      result.push({ time: candles[i].time, upper: null, middle: null, lower: null });
    } else {
      let sumSq = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const diff = candles[j].close - mid[i].value!;
        sumSq += diff * diff;
      }
      const std = Math.sqrt(sumSq / period);
      result.push({
        time: candles[i].time,
        upper: mid[i].value! + stdDev * std,
        middle: mid[i].value!,
        lower: mid[i].value! - stdDev * std,
      });
    }
  }
  return result;
}

// --- RSI (Relative Strength Index) ---
export function rsi(candles: Candle[], period: number = 14): IndicatorPoint[] {
  const result: IndicatorPoint[] = [];
  if (candles.length < period + 1) {
    return candles.map((c) => ({ time: c.time, value: null }));
  }

  // Calculate initial average gain/loss
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = candles[i].close - candles[i - 1].close;
    if (change > 0) avgGain += change;
    else avgLoss += Math.abs(change);
  }
  avgGain /= period;
  avgLoss /= period;

  // First `period` candles have no RSI
  for (let i = 0; i < period; i++) {
    result.push({ time: candles[i].time, value: null });
  }

  // First RSI value
  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result.push({ time: candles[period].time, value: 100 - 100 / (1 + rs) });

  // Subsequent values using smoothed averages
  for (let i = period + 1; i < candles.length; i++) {
    const change = candles[i].close - candles[i - 1].close;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;

    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;

    const rsVal = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result.push({ time: candles[i].time, value: 100 - 100 / (1 + rsVal) });
  }
  return result;
}

// --- MACD ---
export interface MACDPoint {
  time: number;
  macd: number | null;
  signal: number | null;
  histogram: number | null;
}

export function macd(
  candles: Candle[],
  fastPeriod: number = 12,
  slowPeriod: number = 26,
  signalPeriod: number = 9,
): MACDPoint[] {
  const fastEma = ema(candles, fastPeriod);
  const slowEma = ema(candles, slowPeriod);

  // MACD line = fast EMA - slow EMA
  const macdLine: IndicatorPoint[] = candles.map((c, i) => ({
    time: c.time,
    value: fastEma[i].value !== null && slowEma[i].value !== null
      ? fastEma[i].value! - slowEma[i].value!
      : null,
  }));

  // Signal line = EMA of MACD line
  // Build a pseudo-candle array from MACD values for ema calculation
  const macdCandles: Candle[] = macdLine
    .filter((p) => p.value !== null)
    .map((p) => ({ time: p.time, open: p.value!, high: p.value!, low: p.value!, close: p.value! }));

  const signalEma = ema(macdCandles, signalPeriod);

  // Map signal back to full timeline
  const signalMap = new Map<number, number>();
  signalEma.forEach((p) => { if (p.value !== null) signalMap.set(p.time, p.value); });

  return candles.map((c, i) => {
    const m = macdLine[i].value;
    const s = signalMap.get(c.time) ?? null;
    return {
      time: c.time,
      macd: m,
      signal: s,
      histogram: m !== null && s !== null ? m - s : null,
    };
  });
}
