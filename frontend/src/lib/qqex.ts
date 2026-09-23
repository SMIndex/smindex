/**
 * QQEX v6.0 ([Alerts]QQE Cross v6.0 by JustUncleL) — Pine v3 port.
 *
 * Full open/close signal system: QQE core (smoothed-RSI trailing bands) +
 * dual EMA ribbon (16/21/26 and ×anchor 64/84/104) + run-length counters +
 * flat/long/short state machine.
 *
 * Pine-fidelity notes (each deliberate, do not "simplify"):
 *  - wilderRsi is TRUE SMA-seeded Wilder RMA (TradingView rsi()). A first-value
 *    seeded EWM diverges for ~n bars and fails parity on short windows.
 *  - pineEma seeds from the FIRST valid value (Pine ema()), NOT SMA-seeded —
 *    this intentionally differs from hullSuite's emaSeries.
 *  - Band ratchet + trend flip compare against the PREVIOUS bar's opposite band
 *    (Pine shortband[1]/longband[1]); Pine cross() = strict sign change.
 *  - All six signals are run-length counters (==1 means first bar of a run).
 *  - anchor=4 → mult>1 filter branch is the ACTIVE one; the mult==1 branch
 *    differs and is implemented separately (switch on mult).
 *  - Open signals use Pine's [1] refs: qualification at close of bar k paints
 *    the arrow on bar k+1 → arrows never repaint. Entry fill = open of k+1.
 *  - Close signals evaluate on the CURRENT bar close (no [1]); exit fill =
 *    open of j+1. The expanded close condition's asymmetry (XCshort counter >0
 *    vs XClong ==1) is the Pine source's own — kept verbatim.
 *  - ufirst (alert-dedup option) is NOT implemented: the state machine already
 *    ignores opens while in a position, so it cannot affect rendered trades.
 *
 * Warmup: signals are suppressed for the first WARMUP_BARS bars (EMA104 and
 * the double-EMA(27) chain converge slowly; seeding transients must die out).
 */

import type { Candle } from './indicators';
import type { TradeBox } from './hullSuite';

export const QQEX_WARMUP_BARS = 300;

export interface QqexSettings {
  rsiLen: number;       // 14
  sf: number;           // 8
  qqeFactor: number;    // 5.0
  threshold: number;    // 10 → zones 60/40
  fastLen: number;      // 16 (EMA)
  medLen: number;       // 21
  slowLen: number;      // 26
  anchor: number;       // 4 → alt ribbon EMA 64/84/104 (same timeframe, no MTF)
  tradeSignal: 'XC' | 'XQ' | 'XZ';
  useFilter: boolean;   // MA ribbon filter (default false)
  useDfilter: boolean;  // directional filter (default TRUE — active config)
  xfilter: boolean;     // XQ-open-mode extra gate (inactive with XC opens)
  showAltRibbon: boolean; // display-only
  showXc: boolean;      // XC event triangles (Pine sQQEc, default true)
  showXq: boolean;      // XQ event triangles (Pine sQQEx, default true)
  showXz: boolean;      // XZ event triangles (Pine sQQEz, default FALSE)
}

export const DEFAULT_QQEX_SETTINGS: QqexSettings = {
  rsiLen: 14,
  sf: 8,
  qqeFactor: 5.0,
  threshold: 10,
  fastLen: 16,
  medLen: 21,
  slowLen: 26,
  anchor: 4,
  tradeSignal: 'XC',
  useFilter: false,
  useDfilter: true,
  xfilter: true,
  showAltRibbon: true,
  showXc: true,
  showXq: true,
  showXz: false,
};

// ─── Pine-faithful primitives ────────────────────────────────────────────────

/** Pine ema(): alpha = 2/(n+1), seeded from the first valid value. */
export function pineEma(values: Float64Array | number[], period: number): Float64Array {
  const n = values.length;
  const out = new Float64Array(n).fill(NaN);
  if (period <= 0) return out;
  const k = 2 / (period + 1);
  let prev = NaN;
  for (let i = 0; i < n; i++) {
    const v = values[i];
    if (!isFinite(v)) { out[i] = prev; continue; }
    prev = isFinite(prev) ? v * k + prev * (1 - k) : v;
    out[i] = prev;
  }
  return out;
}

/** TradingView-exact rsi(): Wilder RMA (alpha=1/n) on up/down moves, SMA seed. */
export function wilderRsi(closes: number[], n: number): Float64Array {
  const m = closes.length;
  const out = new Float64Array(m).fill(NaN);
  if (m <= n || n <= 0) return out;
  let sumUp = 0;
  let sumDn = 0;
  for (let i = 1; i <= n; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) sumUp += d; else sumDn -= d;
  }
  let avgUp = sumUp / n;
  let avgDn = sumDn / n;
  const toRsi = (u: number, d: number) => (d === 0 ? 100 : u === 0 ? 0 : 100 - 100 / (1 + u / d));
  out[n] = toRsi(avgUp, avgDn);
  for (let i = n + 1; i < m; i++) {
    const d = closes[i] - closes[i - 1];
    const up = d > 0 ? d : 0;
    const dn = d < 0 ? -d : 0;
    avgUp = (avgUp * (n - 1) + up) / n;
    avgDn = (avgDn * (n - 1) + dn) / n;
    out[i] = toRsi(avgUp, avgDn);
  }
  return out;
}

/** Run-length counter: +1 while cond holds, reset to 0 when it breaks. */
function runCounter(cond: (i: number) => boolean, n: number): Int32Array {
  const out = new Int32Array(n);
  for (let i = 0; i < n; i++) out[i] = cond(i) ? (i > 0 ? out[i - 1] : 0) + 1 : 0;
  return out;
}

/** ±1 if strictly rising/falling 3 consecutive bars, else 0 (Pine rising(x,3)). */
function dir3(s: Float64Array): Int8Array {
  const n = s.length;
  const out = new Int8Array(n);
  for (let i = 3; i < n; i++) {
    const a = s[i]; const b = s[i - 1]; const c = s[i - 2]; const d = s[i - 3];
    if (!isFinite(a) || !isFinite(b) || !isFinite(c) || !isFinite(d)) continue;
    if (a > b && b > c && c > d) out[i] = 1;
    else if (a < b && b < c && c < d) out[i] = -1;
  }
  return out;
}

// ─── Result types ────────────────────────────────────────────────────────────

export interface QqexMarker {
  time: number;
  kind: 'open_long' | 'open_short' | 'close_long' | 'close_short';
  price: number;      // fill price (open of the bar after the event)
  pnlPct?: number;    // set on close markers
}

export interface QqexRibbonBar {
  time: number;
  fast: number;
  med: number;
  slow: number;
  neg: boolean;   // direction < 0 (Pine: 0 counts as positive → green/aqua)
}

export interface QqexTriangle {
  time: number;
  kind: 'xc' | 'xq' | 'xz';
  side: 'long' | 'short';
}

export interface QqexResult {
  ribbon: QqexRibbonBar[];      // primary EMA 16/21/26, colored by direction
  ribbonAlt: QqexRibbonBar[];   // anchor EMA 64/84/104, colored by altDirection ([] if mult<=1 or hidden)
  triangles: QqexTriangle[];    // XC/XQ/XZ event marks with Pine's suppression rules
  trades: TradeBox[];       // entry open(k+1) → exit open(j+1); last may be 'open'
  markers: QqexMarker[];
  readout: {
    rsindex: number | null;
    tl: number | null;
    trend: 1 | -1;
    state: 'flat' | 'long' | 'short';
    openSignals: number;    // qualified open events after warmup
  };
}

// ─── Master computation ──────────────────────────────────────────────────────

// minSignalTime: optional warmup anchor (unix seconds). Signals/events are
// suppressed on bars earlier than this, IN ADDITION to the 300-bar index warmup —
// so scroll-back pagination can prepend history without reshuffling which signals
// exist. 0 (default) = index-based warmup only (audit fixtures unchanged).
export function computeQqex(candles: Candle[], s: QqexSettings, maxTrades: number, minSignalTime = 0): QqexResult {
  const n = candles.length;
  const closes = candles.map((c) => c.close);
  const times = candles.map((c) => c.time);
  const wilders = 2 * s.rsiLen - 1;

  // ── QQE core ──
  const r = pineEma(wilderRsi(closes, s.rsiLen), s.sf);
  const atrRsi = new Float64Array(n).fill(NaN);
  for (let i = 1; i < n; i++) {
    if (isFinite(r[i]) && isFinite(r[i - 1])) atrRsi[i] = Math.abs(r[i] - r[i - 1]);
  }
  const darBase = pineEma(pineEma(atrRsi, wilders), wilders);
  const newLb = new Float64Array(n).fill(NaN);
  const newSb = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    const dar = darBase[i] * s.qqeFactor;
    if (isFinite(r[i]) && isFinite(dar)) { newLb[i] = r[i] - dar; newSb[i] = r[i] + dar; }
  }

  // Ratcheting bands + trend (stateful — comparisons with NaN are false, which
  // exactly reproduces Pine's na handling in these conditions).
  const lb = new Float64Array(n).fill(NaN);
  const sb = new Float64Array(n).fill(NaN);
  const trend = new Int8Array(n).fill(1);
  const tl = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (i === 0 || !isFinite(r[i]) || !isFinite(r[i - 1])) {
      lb[i] = newLb[i]; sb[i] = newSb[i];
      if (i > 0) trend[i] = trend[i - 1];
      tl[i] = trend[i] === 1 ? lb[i] : sb[i];
      continue;
    }
    lb[i] = (r[i - 1] > lb[i - 1] && r[i] > lb[i - 1]) ? Math.max(lb[i - 1], newLb[i]) : newLb[i];
    sb[i] = (r[i - 1] < sb[i - 1] && r[i] < sb[i - 1]) ? Math.min(sb[i - 1], newSb[i]) : newSb[i];
    if (i >= 2) {
      const up = (r[i] > sb[i - 1] && r[i - 1] <= sb[i - 2]) || (r[i] < sb[i - 1] && r[i - 1] >= sb[i - 2]);
      const dn = (lb[i - 1] > r[i] && lb[i - 2] <= r[i - 1]) || (lb[i - 1] < r[i] && lb[i - 2] >= r[i - 1]);
      trend[i] = up ? 1 : dn ? -1 : trend[i - 1];
    } else {
      trend[i] = trend[i - 1];
    }
    tl[i] = trend[i] === 1 ? lb[i] : sb[i];
  }

  // ── EMA ribbons ──
  const mult = s.anchor > 0 ? s.anchor : 1;
  const emaF = pineEma(closes, s.fastLen);
  const emaM = pineEma(closes, s.medLen);
  const emaS = pineEma(closes, s.slowLen);
  const emaFa = pineEma(closes, s.fastLen * mult);
  const emaMa = pineEma(closes, s.medLen * mult);
  const emaSa = pineEma(closes, s.slowLen * mult);
  const direction = dir3(emaM);
  const altDirection = dir3(emaMa);

  // ── Run-length counters ──
  const hiZone = 50 + s.threshold;
  const loZone = 50 - s.threshold;
  const cXlong = runCounter((i) => tl[i] < r[i], n);
  const cXshort = runCounter((i) => tl[i] > r[i], n);
  const cZlong = runCounter((i) => r[i] >= 50, n);   // >= on the long side (Pine source)
  const cZshort = runCounter((i) => r[i] < 50, n);
  const cClong = runCounter((i) => r[i] > hiZone, n);
  const cCshort = runCounter((i) => r[i] < loZone, n);

  // ── Entry filters (branch switched on mult, per source) ──
  const fLong = new Array<boolean>(n).fill(true);
  const fShort = new Array<boolean>(n).fill(true);
  for (let i = 0; i < n; i++) {
    const c = closes[i];
    if (s.useFilter) {
      if (mult > 1) {
        fLong[i] &&= emaM[i] > emaMa[i] && c > emaF[i] && emaF[i] > emaM[i];
        fShort[i] &&= emaM[i] < emaMa[i] && c < emaF[i] && emaF[i] < emaM[i];
      } else {
        fLong[i] &&= c > emaM[i] && emaM[i] > emaS[i] && emaF[i] > emaM[i];
        fShort[i] &&= c < emaM[i] && emaM[i] < emaS[i] && emaF[i] < emaM[i];
      }
    }
    if (s.useDfilter) {
      if (mult > 1) {
        fLong[i] &&= direction[i] > 0 && altDirection[i] > 0 && c > emaM[i];
        fShort[i] &&= direction[i] < 0 && altDirection[i] < 0 && c < emaM[i];
      } else {
        fLong[i] &&= direction[i] > 0;
        fShort[i] &&= direction[i] < 0;
      }
    }
  }

  // ── Open qualification at close of bar k (Pine paints the arrow on k+1) ──
  const qLong = new Array<boolean>(n).fill(false);
  const qShort = new Array<boolean>(n).fill(false);
  for (let i = QQEX_WARMUP_BARS; i < n; i++) {
    if (times[i] < minSignalTime) continue;
    if (s.tradeSignal === 'XC') {
      qLong[i] = cClong[i] === 1 && fLong[i];
      qShort[i] = cCshort[i] === 1 && fShort[i];
    } else if (s.tradeSignal === 'XQ') {
      const xfOk = !s.xfilter || r[i] > hiZone || r[i] < loZone;
      qLong[i] = cXlong[i] === 1 && fLong[i] && xfOk;
      qShort[i] = cXshort[i] === 1 && fShort[i] && xfOk;
    } else { // XZ
      qLong[i] = cZlong[i] === 1 && fLong[i];
      qShort[i] = cZshort[i] === 1 && fShort[i];
    }
  }

  // ── Close qualification at close of bar j (exits are NEVER filtered) ──
  // tradeSignal ≠ XQ → Pine's EXPANDED close condition, asymmetry kept verbatim.
  // tradeSignal = XQ → plain first-bar QQE-line cross.
  let clLongCond: (i: number) => boolean;
  let clShortCond: (i: number) => boolean;
  if (s.tradeSignal !== 'XQ') {
    clLongCond = (i) => cXshort[i] === 1 || cZshort[i] > 0 || cCshort[i] > 0;
    clShortCond = (i) => cXlong[i] === 1 || cZlong[i] > 0 || cClong[i] === 1;
  } else {
    clLongCond = (i) => cXshort[i] === 1;
    clShortCond = (i) => cXlong[i] === 1;
  }
  const clLongRun = runCounter(clLongCond, n);
  const clShortRun = runCounter(clShortCond, n);
  const clLong = new Array<boolean>(n).fill(false);
  const clShort = new Array<boolean>(n).fill(false);
  for (let i = QQEX_WARMUP_BARS; i < n; i++) {
    if (times[i] < minSignalTime) continue;
    clLong[i] = clLongRun[i] === 1;
    clShort[i] = clShortRun[i] === 1;
  }

  // ── State machine: 0 flat, 1 long, 2 short. Opens only from flat; close only
  // the matching side; no direct long↔short flip. ──
  const state = new Int8Array(n);
  for (let i = 1; i < n; i++) {
    const prev = state[i - 1];
    if (prev === 0) state[i] = qLong[i] ? 1 : qShort[i] ? 2 : 0;
    else if (prev === 1) state[i] = clLong[i] ? 0 : 1;
    else state[i] = clShort[i] ? 0 : 2;
  }

  // ── Trades + markers from state transitions ──
  // Entry: qualified at close of k → arrow bar & fill = open of k+1 (a bar-k
  // qualification on the still-forming last bar draws nothing yet — appears,
  // already confirmed, when the next bar opens; arrows therefore never repaint).
  // Exit: close event at bar j → marker on j (where TradingView paints it),
  // fill = open of j+1 (or close of j at the live edge).
  const lastIdx = n - 1;
  const trades: TradeBox[] = [];
  const markers: QqexMarker[] = [];
  const openQualBars = new Set<number>();   // qualifying bar k of every actual open (for triangle suppression)
  let openSignals = 0;
  let entryIdx = -1;
  let entrySide: 'long' | 'short' = 'long';
  for (let i = 1; i < n; i++) {
    const prev = state[i - 1];
    const cur = state[i];
    if (prev === 0 && cur !== 0) {
      openSignals++;
      entryIdx = i;
      entrySide = cur === 1 ? 'long' : 'short';
      openQualBars.add(i);
    } else if (prev !== 0 && cur === 0 && entryIdx >= 0) {
      if (entryIdx + 1 > lastIdx) { entryIdx = -1; continue; } // qualified on the live bar, never filled
      const dir = entrySide === 'long' ? 1 : -1;
      const entryPrice = candles[entryIdx + 1].open;
      const exitPrice = i + 1 <= lastIdx ? candles[i + 1].open : candles[i].close;
      const pnlPct = dir * ((exitPrice - entryPrice) / entryPrice) * 100;
      trades.push({
        side: entrySide,
        entryTime: times[entryIdx + 1], entryPrice,
        exitTime: times[i], exitPrice,
        mode: 'backtest', pnlPct, win: pnlPct >= 0, outcome: 'signal',
      });
      markers.push({ time: times[entryIdx + 1], kind: entrySide === 'long' ? 'open_long' : 'open_short', price: entryPrice });
      markers.push({ time: times[i], kind: entrySide === 'long' ? 'close_long' : 'close_short', price: exitPrice, pnlPct });
      entryIdx = -1;
    }
  }
  // Still-open trade at the live edge
  if (entryIdx >= 0 && entryIdx + 1 <= lastIdx) {
    const dir = entrySide === 'long' ? 1 : -1;
    const entryPrice = candles[entryIdx + 1].open;
    const exitPrice = candles[lastIdx].close;
    const pnlPct = dir * ((exitPrice - entryPrice) / entryPrice) * 100;
    trades.push({
      side: entrySide,
      entryTime: times[entryIdx + 1], entryPrice,
      exitTime: times[lastIdx], exitPrice,
      mode: 'backtest', pnlPct, win: pnlPct >= 0, outcome: 'open',
    });
    markers.push({ time: times[entryIdx + 1], kind: entrySide === 'long' ? 'open_long' : 'open_short', price: entryPrice });
  }

  const cappedTrades = maxTrades > 0 && trades.length > maxTrades ? trades.slice(trades.length - maxTrades) : trades;
  const firstShownTime = cappedTrades.length ? cappedTrades[0].entryTime : Infinity;
  const cappedMarkers = markers.filter((m) => m.time >= firstShownTime);

  // ── Ribbons (per-bar direction color; Pine: direction<0 → red/blue, else green/aqua) ──
  const ribbon: QqexRibbonBar[] = [];
  const ribbonAlt: QqexRibbonBar[] = [];
  for (let i = 0; i < n; i++) {
    if (isFinite(emaF[i]) && isFinite(emaM[i]) && isFinite(emaS[i])) {
      ribbon.push({ time: times[i], fast: emaF[i], med: emaM[i], slow: emaS[i], neg: direction[i] < 0 });
    }
    if (s.showAltRibbon && mult > 1 && isFinite(emaFa[i]) && isFinite(emaMa[i]) && isFinite(emaSa[i])) {
      ribbonAlt.push({ time: times[i], fast: emaFa[i], med: emaMa[i], slow: emaSa[i], neg: altDirection[i] < 0 });
    }
  }

  // ── Event triangles with Pine's suppression rules: hide on Open bars; XC wins
  // over XQ; XQ wins over XZ. Aligned to the counter==1 bar (the event bar). ──
  const triangles: QqexTriangle[] = [];
  for (let i = QQEX_WARMUP_BARS; i < n; i++) {
    if (times[i] < minSignalTime || openQualBars.has(i)) continue;
    if (s.showXc) {
      if (cClong[i] === 1) triangles.push({ time: times[i], kind: 'xc', side: 'long' });
      if (cCshort[i] === 1) triangles.push({ time: times[i], kind: 'xc', side: 'short' });
    }
    if (s.showXq) {
      if (cXlong[i] === 1 && cClong[i] !== 1) triangles.push({ time: times[i], kind: 'xq', side: 'long' });
      if (cXshort[i] === 1 && cCshort[i] !== 1) triangles.push({ time: times[i], kind: 'xq', side: 'short' });
    }
    if (s.showXz) {
      if (cZlong[i] === 1 && cClong[i] !== 1 && cXlong[i] !== 1) triangles.push({ time: times[i], kind: 'xz', side: 'long' });
      if (cZshort[i] === 1 && cCshort[i] !== 1 && cXshort[i] !== 1) triangles.push({ time: times[i], kind: 'xz', side: 'short' });
    }
  }

  const lastState = state[lastIdx] ?? 0;
  return {
    ribbon,
    ribbonAlt,
    triangles,
    trades: cappedTrades,
    markers: cappedMarkers,
    readout: {
      rsindex: isFinite(r[lastIdx]) ? r[lastIdx] : null,
      tl: isFinite(tl[lastIdx]) ? tl[lastIdx] : null,
      trend: (trend[lastIdx] === -1 ? -1 : 1),
      state: lastState === 1 ? 'long' : lastState === 2 ? 'short' : 'flat',
      openSignals,
    },
  };
}
