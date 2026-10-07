/**
 * Hull Entry Suite — port of the Pine v4 indicator.
 * Math: HMA/EHMA/THMA + Wilder's ADX + session VWAP + S/R + market structure
 * + HTF trend → BUY/SELL signals with cooldown.
 *
 * All functions take Candle[] and return arrays aligned 1:1 with the input
 * (null for periods before the lookback window is full).
 */

import type { Candle } from './indicators';
import { DEFAULT_QQEX_SETTINGS, type QqexSettings } from './qqex';

export interface HullSuiteSettings {
  // Hull
  hullMode: 'Hma' | 'Ehma' | 'Thma';
  hullLength: number;
  hullLengthMult: number;
  useHtfHull: boolean;
  hullHtf: string; // e.g. '240' (minutes)
  switchColor: boolean;
  showBand: boolean;
  hullThickness: number;
  bandTransparency: number;
  // Filters
  useVWAP: boolean;
  useVolume: boolean;
  volumeLen: number;
  volumeMult: number;
  useADX: boolean;
  adxLen: number;
  adxMin: number;
  useStructure: boolean;
  structureLen: number;
  useSR: boolean;
  srLen: number;
  entryMode: 'Breakout' | 'Pullback';
  pullbackBuffer: number; // percent
  useHTFTrend: boolean;
  trendHtf: string; // e.g. '60' (minutes)
  htfEmaLen: number;
  // Signals
  showBuySell: boolean;
  showArrows: boolean;
  cooldownBars: number;
  // Strategy variant + trade-box visualization
  strategyVariant: 'entry_suite' | 'crossover' | 'qqex_v6';  // filtered 7-condition / pure Hull crossover / QQE Cross v6
  qqex: QqexSettings;                             // QQEX v6 parameters (used when variant = qqex_v6)
  showTradeBoxes: boolean;
  boxMode: 'backtest' | 'target';                 // A: entry→exit P&L; B: ATR target/stop
  atrLen: number;
  atrTargetMult: number;
  atrStopMult: number;
  maxBoxes: number;
  filtersV?: number; // migration marker for the loosened Entry Suite defaults
}

export const DEFAULT_HULL_SETTINGS: HullSuiteSettings = {
  hullMode: 'Hma',
  hullLength: 55,
  hullLengthMult: 1.0,
  useHtfHull: false,
  hullHtf: '240',
  switchColor: true,
  showBand: true,
  hullThickness: 3,
  bandTransparency: 50,
  // Entry Suite core = Hull + VWAP + ADX (produces usable signals). Structure /
  // S-R breakout / Volume / HTF are OPTIONAL confirmations, OFF by default —
  // requiring all of them at once fired almost never.
  useVWAP: true,
  useVolume: false,
  volumeLen: 20,
  volumeMult: 1.2,
  useADX: true,
  adxLen: 14,
  adxMin: 20,
  useStructure: false,
  structureLen: 20,
  useSR: false,
  srLen: 50,
  entryMode: 'Breakout',
  pullbackBuffer: 0.20,
  useHTFTrend: false,
  trendHtf: '60',
  htfEmaLen: 200,
  showBuySell: true,
  showArrows: true,
  cooldownBars: 12,
  strategyVariant: 'entry_suite',
  qqex: DEFAULT_QQEX_SETTINGS,
  showTradeBoxes: true,
  boxMode: 'backtest',
  atrLen: 14,
  atrTargetMult: 3,
  atrStopMult: 1.5,
  maxBoxes: 6,
  filtersV: 2,
};

// ─── Moving averages ──────────────────────────────────────────────────────────

export function wma(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const weightSum = (period * (period + 1)) / 2;
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    let ok = true;
    for (let j = 0; j < period; j++) {
      const v = values[i - j];
      if (v === null || v === undefined || !isFinite(v)) { ok = false; break; }
      // Most-recent value carries highest weight (= period)
      sum += v * (period - j);
    }
    out[i] = ok ? sum / weightSum : null;
  }
  return out;
}

function emaSeries(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const k = 2 / (period + 1);
  // Seed with SMA of first `period` non-null values
  let firstIdx = -1;
  let sum = 0;
  let count = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined || !isFinite(v)) continue;
    sum += v;
    count++;
    if (count === period) { firstIdx = i; out[i] = sum / period; break; }
  }
  if (firstIdx < 0) return out;
  for (let i = firstIdx + 1; i < values.length; i++) {
    const v = values[i];
    const prev = out[i - 1];
    if (v === null || v === undefined || !isFinite(v) || prev === null) { out[i] = prev; continue; }
    out[i] = v * k + prev * (1 - k);
  }
  return out;
}

// Wilder's RMA (used by ADX) — equivalent to EMA with alpha = 1/period
function rmaSeries(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const k = 1 / period;
  let firstIdx = -1;
  let sum = 0;
  let count = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === null || v === undefined || !isFinite(v)) continue;
    sum += v;
    count++;
    if (count === period) { firstIdx = i; out[i] = sum / period; break; }
  }
  if (firstIdx < 0) return out;
  for (let i = firstIdx + 1; i < values.length; i++) {
    const v = values[i];
    const prev = out[i - 1];
    if (v === null || v === undefined || !isFinite(v) || prev === null) { out[i] = prev; continue; }
    out[i] = v * k + prev * (1 - k);
  }
  return out;
}

// ─── Hull variants ────────────────────────────────────────────────────────────

export function hma(src: number[], length: number): (number | null)[] {
  const halfLen = Math.max(1, Math.floor(length / 2));
  const sqrtLen = Math.max(1, Math.round(Math.sqrt(length)));
  const wmaHalf = wma(src, halfLen);
  const wmaFull = wma(src, length);
  const diff = src.map((_, i) => {
    const a = wmaHalf[i]; const b = wmaFull[i];
    return a !== null && b !== null ? 2 * a - b : null;
  });
  return wma(diff, sqrtLen);
}

export function ehma(src: number[], length: number): (number | null)[] {
  const halfLen = Math.max(1, Math.floor(length / 2));
  const sqrtLen = Math.max(1, Math.round(Math.sqrt(length)));
  const eHalf = emaSeries(src, halfLen);
  const eFull = emaSeries(src, length);
  const diff = src.map((_, i) => {
    const a = eHalf[i]; const b = eFull[i];
    return a !== null && b !== null ? 2 * a - b : null;
  });
  return emaSeries(diff, sqrtLen);
}

export function thma(src: number[], length: number): (number | null)[] {
  const thirdLen = Math.max(1, Math.floor(length / 3));
  const halfLen = Math.max(1, Math.floor(length / 2));
  const wThird = wma(src, thirdLen);
  const wHalf = wma(src, halfLen);
  const wFull = wma(src, length);
  const diff = src.map((_, i) => {
    const a = wThird[i]; const b = wHalf[i]; const c = wFull[i];
    return a !== null && b !== null && c !== null ? a * 3 - b - c : null;
  });
  return wma(diff, length);
}

export function computeHull(src: number[], mode: HullSuiteSettings['hullMode'], length: number): (number | null)[] {
  if (mode === 'Hma') return hma(src, length);
  if (mode === 'Ehma') return ehma(src, length);
  // THMA in the Pine reference is called with length/2 inside Mode()
  return thma(src, Math.max(2, Math.floor(length / 2)));
}

// ─── ADX (Wilder) ─────────────────────────────────────────────────────────────

export function adx(candles: Candle[], length: number): (number | null)[] {
  const n = candles.length;
  const plusDM: (number | null)[] = new Array(n).fill(null);
  const minusDM: (number | null)[] = new Array(n).fill(null);
  const tr: (number | null)[] = new Array(n).fill(null);
  for (let i = 1; i < n; i++) {
    const c = candles[i]; const p = candles[i - 1];
    const upMove = c.high - p.high;
    const downMove = p.low - c.low;
    plusDM[i] = upMove > downMove && upMove > 0 ? upMove : 0;
    minusDM[i] = downMove > upMove && downMove > 0 ? downMove : 0;
    tr[i] = Math.max(
      c.high - c.low,
      Math.abs(c.high - p.close),
      Math.abs(c.low - p.close),
    );
  }
  const plusRMA = rmaSeries(plusDM, length);
  const minusRMA = rmaSeries(minusDM, length);
  const trRMA = rmaSeries(tr, length);
  const dx: (number | null)[] = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const pdi = plusRMA[i]; const mdi = minusRMA[i]; const trv = trRMA[i];
    if (pdi === null || mdi === null || trv === null || trv === 0) continue;
    const plusDI = (100 * pdi) / trv;
    const minusDI = (100 * mdi) / trv;
    const denom = Math.max(plusDI + minusDI, 1);
    dx[i] = (100 * Math.abs(plusDI - minusDI)) / denom;
  }
  return rmaSeries(dx, length);
}

// ─── Session VWAP (resets at UTC midnight, common for crypto) ─────────────────

export function vwap(candles: Candle[], volumes: number[]): (number | null)[] {
  const out: (number | null)[] = new Array(candles.length).fill(null);
  let cumPV = 0;
  let cumV = 0;
  let curDay = -1;
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i];
    // candle.time is unix seconds; bucket by UTC day
    const day = Math.floor(c.time / 86400);
    if (day !== curDay) { cumPV = 0; cumV = 0; curDay = day; }
    // Pine `vwap(src)` uses the passed source (here: close), not hlc3.
    const price = c.close;
    const v = volumes[i] || 0;
    cumPV += price * v;
    cumV += v;
    out[i] = cumV > 0 ? cumPV / cumV : null;
  }
  return out;
}

// ─── Rolling highest / lowest (Pine `highest` / `lowest`) ─────────────────────

export function rollingHighest(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let h = -Infinity;
    for (let j = i - period + 1; j <= i; j++) if (values[j] > h) h = values[j];
    out[i] = h;
  }
  return out;
}

export function rollingLowest(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let l = Infinity;
    for (let j = i - period + 1; j <= i; j++) if (values[j] < l) l = values[j];
    out[i] = l;
  }
  return out;
}

// ─── SMA on number[] (for volume) ─────────────────────────────────────────────

function smaArr(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let s = 0;
    for (let j = i - period + 1; j <= i; j++) s += values[j];
    out[i] = s / period;
  }
  return out;
}

// ─── Map HTF series onto base timeframe (forward-fill) ────────────────────────
// htfPoints must be sorted ascending by time.
export function alignHTFToBase(
  baseTimes: number[],
  htfPoints: { time: number; value: number | null }[],
): (number | null)[] {
  const out: (number | null)[] = new Array(baseTimes.length).fill(null);
  if (!htfPoints.length) return out;
  let j = 0;
  let lastVal: number | null = null;
  for (let i = 0; i < baseTimes.length; i++) {
    const t = baseTimes[i];
    while (j < htfPoints.length && htfPoints[j].time <= t) {
      if (htfPoints[j].value !== null && isFinite(htfPoints[j].value as number)) lastVal = htfPoints[j].value;
      j++;
    }
    out[i] = lastVal;
  }
  return out;
}

// ─── Master computation ──────────────────────────────────────────────────────

export interface HullSignalBar {
  time: number;
  side: 'buy' | 'sell';
}

export interface HullPlotPoint { time: number; value: number; }

export interface HullSuiteResult {
  // Plot lines (each with its color hint via `mhullBull` series so the chart
  // can split into bullish / bearish colored segments).
  mhull: HullPlotPoint[];               // line aligned to base candle time
  shull: HullPlotPoint[];               // SHULL = HULL shifted +2
  hullBullAt: boolean[];                // per base candle: true if MHULL>SHULL (for color)
  vwap: HullPlotPoint[];
  resistance: HullPlotPoint[];
  support: HullPlotPoint[];
  htfEma: HullPlotPoint[];
  signals: HullSignalBar[];            // filtered "Entry Suite" signals (7 conditions + cooldown)
  crossoverSignals: HullSignalBar[];   // pure Hull crossover (LuxAlgo "Strategy" variant)
}

export interface ComputeArgs {
  baseCandles: Candle[];                // current chart timeframe
  baseVolumes: number[];                // volumes aligned to baseCandles
  htfHullSrc?: { time: number; value: number | null }[]; // HTF source candles' computed Hull series (already aligned w/ HTF times)
  htfTrendCandles?: Candle[];           // HTF candles for EMA(htfEmaLen)
  settings: HullSuiteSettings;
}

export function computeHullSuite(args: ComputeArgs): HullSuiteResult {
  const { baseCandles, baseVolumes, htfHullSrc, htfTrendCandles, settings: s } = args;
  const n = baseCandles.length;
  const closes = baseCandles.map((c) => c.close);
  const highs = baseCandles.map((c) => c.high);
  const lows = baseCandles.map((c) => c.low);
  const baseTimes = baseCandles.map((c) => c.time);

  // ── HULL ──
  const baseHull = computeHull(closes, s.hullMode, Math.max(2, Math.floor(s.hullLength * s.hullLengthMult)));
  // Effective HULL series (either base-tf or HTF mapped to base)
  let HULL: (number | null)[];
  if (s.useHtfHull && htfHullSrc && htfHullSrc.length) {
    HULL = alignHTFToBase(baseTimes, htfHullSrc);
  } else {
    HULL = baseHull;
  }
  // MHULL = HULL[0], SHULL = HULL[2] (i.e. HULL shifted by 2 bars)
  const mhullVals: (number | null)[] = HULL;
  const shullVals: (number | null)[] = new Array(n).fill(null);
  for (let i = 2; i < n; i++) shullVals[i] = HULL[i - 2];

  // ── VWAP ──
  const vwapVals = vwap(baseCandles, baseVolumes);

  // ── Volume filter (SMA + multiplier) ──
  const volSma = smaArr(baseVolumes, s.volumeLen);
  const volumeConfirm: boolean[] = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    const a = volSma[i];
    volumeConfirm[i] = a !== null ? baseVolumes[i] > a * s.volumeMult : false;
  }

  // ── ADX ──
  const adxVals = adx(baseCandles, s.adxLen);

  // ── Market Structure: highest(high, len)[1] / lowest(low, len)[1] ──
  const structHi = rollingHighest(highs, s.structureLen);
  const structLo = rollingLowest(lows, s.structureLen);
  // Shift by 1 (Pine [1])
  const structHiSh: (number | null)[] = new Array(n).fill(null);
  const structLoSh: (number | null)[] = new Array(n).fill(null);
  for (let i = 1; i < n; i++) { structHiSh[i] = structHi[i - 1]; structLoSh[i] = structLo[i - 1]; }

  // ── Support / Resistance: highest(high, srLen)[1] / lowest(low, srLen)[1] ──
  const resHi = rollingHighest(highs, s.srLen);
  const supLo = rollingLowest(lows, s.srLen);
  const resistanceShifted: (number | null)[] = new Array(n).fill(null);
  const supportShifted: (number | null)[] = new Array(n).fill(null);
  for (let i = 1; i < n; i++) { resistanceShifted[i] = resHi[i - 1]; supportShifted[i] = supLo[i - 1]; }

  // ── HTF EMA (on HTF closes) mapped to base ──
  let htfEmaAligned: (number | null)[] = new Array(n).fill(null);
  if (s.useHTFTrend && htfTrendCandles && htfTrendCandles.length) {
    const htfCloses = htfTrendCandles.map((c) => c.close);
    const htfEmaVals = emaSeries(htfCloses, s.htfEmaLen);
    const htfPoints = htfTrendCandles.map((c, i) => ({ time: c.time, value: htfEmaVals[i] }));
    htfEmaAligned = alignHTFToBase(baseTimes, htfPoints);
  }
  // HTF close also mapped (for the trend gate)
  let htfCloseAligned: (number | null)[] = new Array(n).fill(null);
  if (s.useHTFTrend && htfTrendCandles && htfTrendCandles.length) {
    const pts = htfTrendCandles.map((c) => ({ time: c.time, value: c.close }));
    htfCloseAligned = alignHTFToBase(baseTimes, pts);
  }

  // ── Build longEntry / shortEntry per bar ──
  const longArr: boolean[] = new Array(n).fill(false);
  const shortArr: boolean[] = new Array(n).fill(false);
  for (let i = 0; i < n; i++) {
    const c = baseCandles[i].close;
    const o = baseCandles[i].open;
    const h = baseCandles[i].high;
    const l = baseCandles[i].low;
    const mh = mhullVals[i]; const sh = shullVals[i];
    const hullBull = mh !== null && sh !== null && mh > sh;
    const hullBear = mh !== null && sh !== null && mh < sh;

    const vw = vwapVals[i];
    const vwLong = s.useVWAP ? (vw !== null && c > vw) : true;
    const vwShort = s.useVWAP ? (vw !== null && c < vw) : true;

    const vol = s.useVolume ? volumeConfirm[i] : true;

    const adxV = adxVals[i];
    const adxOk = s.useADX ? (adxV !== null && adxV >= s.adxMin) : true;

    const sH = structHiSh[i]; const sL = structLoSh[i];
    const structLong = s.useStructure ? (sH !== null && c > sH) : true;
    const structShort = s.useStructure ? (sL !== null && c < sL) : true;

    const r = resistanceShifted[i]; const sup = supportShifted[i];
    let srLong = true; let srShort = true;
    if (s.useSR) {
      if (s.entryMode === 'Breakout') {
        srLong = r !== null && c > r;
        srShort = sup !== null && c < sup;
      } else {
        // Pullback
        const buf = s.pullbackBuffer / 100;
        const nearSupport = sup !== null && l <= sup * (1 + buf);
        const nearResistance = r !== null && h >= r * (1 - buf);
        srLong = nearSupport && c > o && (vw !== null && c > vw);
        srShort = nearResistance && c < o && (vw !== null && c < vw);
      }
    }

    const htfClose = htfCloseAligned[i]; const htfEma = htfEmaAligned[i];
    const htfBull = s.useHTFTrend ? (htfClose !== null && htfEma !== null && htfClose > htfEma) : true;
    const htfBear = s.useHTFTrend ? (htfClose !== null && htfEma !== null && htfClose < htfEma) : true;

    longArr[i] = hullBull && vwLong && vol && adxOk && structLong && srLong && htfBull;
    shortArr[i] = hullBear && vwShort && vol && adxOk && structShort && srShort && htfBear;
  }

  // ── Raw edges + cooldown ──
  let lastSignalBar = -Infinity;
  let lastDir: 0 | 1 | -1 = 0;
  const signals: HullSignalBar[] = [];
  for (let i = 1; i < n; i++) {
    const rawBuy = longArr[i] && !longArr[i - 1];
    const rawSell = shortArr[i] && !shortArr[i - 1];
    const canSignal = i - lastSignalBar > s.cooldownBars;
    if (rawBuy && canSignal && lastDir !== 1) {
      signals.push({ time: baseTimes[i], side: 'buy' });
      lastSignalBar = i; lastDir = 1;
    } else if (rawSell && canSignal && lastDir !== -1) {
      signals.push({ time: baseTimes[i], side: 'sell' });
      lastSignalBar = i; lastDir = -1;
    }
  }

  // ── Format outputs (drop null leading points) ──
  const toPoints = (arr: (number | null)[]): HullPlotPoint[] =>
    arr.map((v, i) => v !== null && isFinite(v) ? { time: baseTimes[i], value: v as number } : null).filter(Boolean) as HullPlotPoint[];

  const hullBullAt = mhullVals.map((m, i) => {
    const sh = shullVals[i];
    return m !== null && sh !== null && m > sh;
  });

  // Pure Hull crossover signals (LuxAlgo "Hull Suite Strategy" variant): buy on
  // MHULL crossing above SHULL, sell on crossing below. A cooldown (min bars
  // between signals) suppresses rapid re-flips / whipsaws in chop.
  const crossoverSignals: HullSignalBar[] = [];
  let lastCrossBar = -Infinity;
  for (let i = 1; i < n; i++) {
    const mPrev = mhullVals[i - 1]; const sPrev = shullVals[i - 1];
    const m = mhullVals[i]; const sh = shullVals[i];
    if (mPrev === null || sPrev === null || m === null || sh === null) continue;
    const crossUp = mPrev <= sPrev && m > sh;
    const crossDn = mPrev >= sPrev && m < sh;
    if (!crossUp && !crossDn) continue;
    if (i - lastCrossBar <= s.cooldownBars) continue; // whipsaw guard
    crossoverSignals.push({ time: baseTimes[i], side: crossUp ? 'buy' : 'sell' });
    lastCrossBar = i;
  }

  return {
    mhull: toPoints(mhullVals),
    shull: toPoints(shullVals),
    hullBullAt,
    vwap: s.useVWAP ? toPoints(vwapVals) : [],
    resistance: s.useSR ? toPoints(resistanceShifted) : [],
    support: s.useSR ? toPoints(supportShifted) : [],
    htfEma: s.useHTFTrend ? toPoints(htfEmaAligned) : [],
    signals,
    crossoverSignals,
  };
}

// ─── ATR (Wilder) — for target/stop boxes ─────────────────────────────────────

export function atr(candles: Candle[], length: number): (number | null)[] {
  const n = candles.length;
  const tr: (number | null)[] = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const c = candles[i];
    if (i === 0) { tr[i] = c.high - c.low; continue; }
    const p = candles[i - 1];
    tr[i] = Math.max(c.high - c.low, Math.abs(c.high - p.close), Math.abs(c.low - p.close));
  }
  return rmaSeries(tr, length);
}

// ─── Trade boxes ──────────────────────────────────────────────────────────────
// Hull defines NO target/stop — exit is the opposite signal. So:
//  • 'backtest' mode: box from entry to the exit (next opposite signal), P&L labelled.
//  • 'target'   mode: forward target/stop derived from ATR (our logic, not Hull's),
//    resolved when price touches target/stop or the opposite signal fires.

export interface TradeBox {
  side: 'long' | 'short';
  entryTime: number;
  entryPrice: number;
  exitTime: number;
  exitPrice: number;
  mode: 'backtest' | 'target';
  target?: number;
  stop?: number;
  pnlPct?: number;
  win?: boolean;
  outcome: 'target' | 'stop' | 'signal' | 'open';
}

export interface TradeBoxArgs {
  candles: Candle[];
  signals: HullSignalBar[];       // the selected variant's signals (entries)
  mode: 'backtest' | 'target';
  atrLen: number;
  atrTargetMult: number;
  atrStopMult: number;
  maxBoxes: number;
}

export function buildTradeBoxes(args: TradeBoxArgs): TradeBox[] {
  const { candles, signals, mode, atrLen, atrTargetMult, atrStopMult, maxBoxes } = args;
  if (!candles.length || !signals.length) return [];
  const timeToIdx = new Map<number, number>();
  candles.forEach((c, i) => timeToIdx.set(c.time, i));
  const atrVals = mode === 'target' ? atr(candles, atrLen) : [];
  const lastIdx = candles.length - 1;

  const boxes: TradeBox[] = [];
  for (let k = 0; k < signals.length; k++) {
    const sig = signals[k];
    const entryIdx = timeToIdx.get(sig.time);
    if (entryIdx === undefined) continue;
    const side: 'long' | 'short' = sig.side === 'buy' ? 'long' : 'short';
    const dir = side === 'long' ? 1 : -1;
    const entryPrice = candles[entryIdx].close;

    // The next opposite signal bounds the trade (Hull's real exit).
    let oppIdx = lastIdx;
    for (let j = k + 1; j < signals.length; j++) {
      if (signals[j].side !== sig.side) {
        const oi = timeToIdx.get(signals[j].time);
        if (oi !== undefined) oppIdx = oi;
        break;
      }
    }

    // Every trade carries its own ATR target + stop (so per-trade TP/SL can be
    // drawn for BOTH modes). ATR may be null very early in the series.
    const a = atrVals[entryIdx];
    const hasAtr = a !== null && a !== undefined && isFinite(a) && a > 0;
    const target = hasAtr ? entryPrice + dir * atrTargetMult * (a as number) : undefined;
    const stop = hasAtr ? entryPrice - dir * atrStopMult * (a as number) : undefined;

    if (mode === 'target' && target !== undefined && stop !== undefined) {
      // Exit at the FIRST of target-touch / stop-touch / opposite signal.
      let exitIdx = oppIdx;
      let exitPrice = candles[oppIdx].close;
      let outcome: TradeBox['outcome'] = oppIdx === lastIdx ? 'open' : 'signal';
      for (let j = entryIdx + 1; j <= oppIdx; j++) {
        const c = candles[j];
        const hitTarget = side === 'long' ? c.high >= target : c.low <= target;
        const hitStop = side === 'long' ? c.low <= stop : c.high >= stop;
        if (hitStop) { exitIdx = j; exitPrice = stop; outcome = 'stop'; break; } // conservative: stop first
        if (hitTarget) { exitIdx = j; exitPrice = target; outcome = 'target'; break; }
      }
      const pnlPct = dir * ((exitPrice - entryPrice) / entryPrice) * 100;
      boxes.push({
        side, entryTime: candles[entryIdx].time, entryPrice,
        exitTime: candles[exitIdx].time, exitPrice, mode,
        target, stop, outcome, pnlPct, win: pnlPct >= 0,
      });
    } else {
      // P&L mode (or no ATR yet): exit at the opposite signal.
      const exitPrice = candles[oppIdx].close;
      const pnlPct = dir * ((exitPrice - entryPrice) / entryPrice) * 100;
      boxes.push({
        side, entryTime: candles[entryIdx].time, entryPrice,
        exitTime: candles[oppIdx].time, exitPrice, mode,
        target, stop, pnlPct, win: pnlPct >= 0,
        outcome: oppIdx === lastIdx ? 'open' : 'signal',
      });
    }
  }

  // Keep only the most recent N to avoid clutter.
  return maxBoxes > 0 && boxes.length > maxBoxes ? boxes.slice(boxes.length - maxBoxes) : boxes;
}

// Convenience: compute the HTF source HULL series so the caller can pass it in
export function computeHTFHullSrc(
  htfCandles: Candle[],
  mode: HullSuiteSettings['hullMode'],
  length: number,
): { time: number; value: number | null }[] {
  const src = htfCandles.map((c) => c.close);
  const h = computeHull(src, mode, length);
  return htfCandles.map((c, i) => ({ time: c.time, value: h[i] }));
}
