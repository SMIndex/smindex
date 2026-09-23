// TP/SL trigger input logic for the Copy Live Trade modal (Part B bug 1).
//
// Owner-reproduced defect: dollar PRICES typed into %-mode fields produced
// absurd previews ("trigger $41,770,209.6") before the zero-guard fired.
// Rules encoded here (pure + unit-tested):
//   * price mode is the DEFAULT (traders think in prices)
//   * % mode hard bounds: SL 0.05-99, TP 0.05-300 — outside => inline error,
//     NO preview
//   * price mode: trigger must be on the correct side of the ref AND within a
//     sane band (0.2x-5x ref) — outside => inline error, NO preview
//   * cross-mode detection: a %-mode value within +/-20% of the ref price
//     LOOKS like a price — offer a one-click switch preserving the value
//   * a computed trigger outside the sane band must NEVER render as a preview
export type TpslKind = 'tp' | 'sl';
export type TpslMode = 'price' | 'pct';

export const SL_PCT_MIN = 0.05;
export const SL_PCT_MAX = 99;
export const TP_PCT_MIN = 0.05;
export const TP_PCT_MAX = 300;
export const PRICE_BAND_LO = 0.2;   // x ref
export const PRICE_BAND_HI = 5;    // x ref

export interface TriggerResult {
  price: number | null;             // valid computed trigger, else null
  error: string | null;             // inline error at the input (no preview)
  looksLikePrice?: boolean;         // %-mode value that resembles the ref price
}

const ok = (price: number): TriggerResult => ({ price, error: null });
const err = (error: string): TriggerResult => ({ price: null, error });

function band(px: number, ref: number): TriggerResult {
  if (ref > 0 && (px < ref * PRICE_BAND_LO || px > ref * PRICE_BAND_HI)) {
    return err(`outside the sane range (${PRICE_BAND_LO}×–${PRICE_BAND_HI}× current price)`);
  }
  return ok(px);
}

export function computeTrigger(
  kind: TpslKind, mode: TpslMode, raw: string,
  ref: number, side: 'long' | 'short',
): TriggerResult {
  const t = raw.trim();
  if (!t) return { price: null, error: null };            // empty = no trigger
  const v = parseFloat(t);
  if (!Number.isFinite(v) || v <= 0) return err('enter a positive number');
  if (ref <= 0) return { price: null, error: null };      // no ref yet — wait
  const sideSign = side === 'long' ? 1 : -1;

  if (mode === 'pct') {
    // the exact live mistake: a "%" that is actually a price
    if (Math.abs(v - ref) / ref <= 0.2) {
      return { price: null, error: null, looksLikePrice: true };
    }
    const [min, max] = kind === 'tp' ? [TP_PCT_MIN, TP_PCT_MAX] : [SL_PCT_MIN, SL_PCT_MAX];
    if (v < min || v > max) return err(kind === 'tp' ? `must be ${min}–${max}%` : `max ${SL_PCT_MAX}%`);
    const px = kind === 'tp' ? ref * (1 + sideSign * v / 100) : ref * (1 - sideSign * v / 100);
    if (!(px > 0)) return err('computes to zero or below');
    return band(px, ref);
  }

  // price mode: explicit side validation (identical semantics to % mode,
  // where the side is correct by construction)
  const wrongSide = kind === 'tp'
    ? (sideSign > 0 ? v <= ref : v >= ref)
    : (sideSign > 0 ? v >= ref : v <= ref);
  if (wrongSide) {
    const dir = (kind === 'tp') === (side === 'long') ? 'above' : 'below';
    return err(`${kind.toUpperCase()} must be ${dir} the entry price`);
  }
  return band(v, ref);
}
