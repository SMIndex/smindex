// Generated-sentence module for the Analytics asset page (verdict-first
// redesign). ALL page prose comes from the fixed templates below — assembled
// from computed values only, no free text, no speculation beyond the data.
//
// TEMPLATE LIST (documented per spec pt.8; {x} = computed slot):
//  H1 net_long   "Smart money is net long {asset}: {nl} long vs {ns} short across {n} wallets."
//  H2 net_short  "Smart money is net short {asset}: {ns} short vs {nl} long across {n} wallets."
//  H3 balanced   "Smart money is split on {asset}: {nl} long vs {ns} short across {n} wallets."
//  H4 low-sample "Small sample: {n} wallet(s) hold {asset} — read with care."
//  H5 empty      "No tracked-cohort positions in {asset} under the current filters."
//  S1 divergence "More wallets are {countSide}, but whales drive the {notionalSide} side."
//  S2 losing-pnl "{Side}s are underwater {amt} and holding."
//  S3 agree      "Count and dollar weighting agree — {pct}% of positioned wallets are {side}."
//  S4 neutral    "Small sample: {n} wallets."           (low-sample subtext)
//  G1 hist-long  "Most longs entered near {px} — {in profit|underwater}."
//  G2 hist-short "Shorts entered {lo}–{hi}."
//  G3 hist-none  "No {side} entries in this slice."
//  A1 age-fresh  "{n} new entr{y|ies} today — fresh conviction."
//  A2 age-stale  "No fresh entries today — old positioning."
//  A3 age-dark   "Position ages unknown for this slice."
//  V1 venue      "Venue OI {voi} — this cohort holds {share}% of it · funding {f}%/h."
//  V2 venue-none "Venue context unavailable this cycle."
//  L1 liq-pocket "Largest liquidation pocket: {amt} at {lo}–{hi}, {below|above} price."
//  L2 liq-none   "No cohort liquidation prices in this slice."
//  C1 crowd      "{n} wallets crowded into the same {side} near {px} ({amt} combined)."
//  C2 crowd-none "No crowded trades in this slice (needs {k}+ wallets within ±{tol}% entries)."
//  T1 trig-sparse "Sparse — {x} of {y} recently-checked wallets run resting TP/SL; most of this cohort manages exits without on-book triggers."
//  T2 trig-none   "No trigger observations yet — coverage builds as profiles are viewed."
//  F1 fresh-stat  "Fresh conviction: {pct}% of dated positions opened in the last 24h."
//  SQ1 squeeze    "A move to {px} squeezes {amt} of {short|long}s."   (largest non-far cluster per side)
//  M1 mover       "{flipped|opened|closed} {ASSET} {side} · {amt}" / "{grew|cut} {ASSET} {side} {+|−}{amt}"
//  DV1 dv-funding "{ASSET}: cohort net {side} while funding pays the {side}s — contrarian conviction."
//  DV2 dv-price   "{ASSET}: price {chg}% in 24h against a net-{side} cohort — {accumulation into weakness|distribution into strength}."
//  DV0 dv-none    "No notable divergences right now."
//  CV1 conviction "uses {pct}% of account as margin"  (margin = notional ÷ leverage; display capped 999%+)
//  CV2 mm-variant "{pct}% margin — routine for this book."
//  TL1 tile       "net {long|short} {amt} · OI {amt}"   TL2 balanced "balanced · OI {amt}"
//  R1 rotation    "de-risking toward majors" | "rotating into alts"
//  EE1 churn      "No {entrants|exits} in 24h — low churn."
//  TR0 collecting "Track record: collecting — {n} observations over {d} days; publishes at {minN} obs / {minD} days."
//  TR1 published  "When SMI read {bucket}, {asset} moved {med}% median over {h} (hit {hit}%, n={n})."
//  CTX1 stance    "Smart money is net {LONG|SHORT} {asset} (SMI {v}[ · calibrating])" / split variant
//  CTX2 crowd     "your trade is {WITH|AGAINST} a {n}-wallet crowd near {px}"
//  CTX3 funding   "HL funding {pays|costs} your side {pct}%/h"
//  CTX0 unavail   "Smart money context unavailable."
//  CP1 copyable   "Copyable on Perpl · {n} leader(s) hold(s) this position"
//  WD1 weighting  "Long share: {a}% by dollars, {b}% by count, {c}% by quality — {agree|lean read}."
//  QS1 quality    "{wr}% ({n} trades) · PF {pf|∞} · maxDD {dd}"   (win rate never without PF once PF exists)
//  FA1 flip       "flip acc {pct}% (n={n})" | "(unrated)"
//
// Rules: BALANCED = |notional skew| <= DEAD_ZONE (±10%). No sentence renders
// from below-minimum samples (MIN_SAMPLE wallets) — H4/S4 neutral fallback.
// Losing-side uPnL is "material" (S2) when |uPnL| >= UPNL_MATERIAL_ABS AND
// >= UPNL_MATERIAL_FRAC of that side's notional.

export const DEAD_ZONE = 0.10;
export const MIN_SAMPLE = 5;
export const UPNL_MATERIAL_ABS = 10_000;     // $
export const UPNL_MATERIAL_FRAC = 0.02;      // 2% of side notional

export type Stance = 'net_long' | 'net_short' | 'balanced';

export interface PositioningIn {
  wallets_long: number;
  wallets_short: number;
  notional_long: number;
  notional_short: number;
  upnl_long: number;
  upnl_short: number;
}

// $62k / $1.2M / $97 / $0.0049 — human price for axis labels and sentences
export function humanPx(px: number): string {
  if (!isFinite(px)) return '—';
  const abs = Math.abs(px);
  if (abs >= 1_000_000) return `$${(px / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `$${Math.round(px / 1000)}k`;
  if (abs >= 1_000) return `$${(px / 1000).toFixed(1)}k`;
  if (abs >= 1) return `$${px.toFixed(px < 100 ? 2 : 0)}`;
  return `$${px.toPrecision(2)}`;
}

export function humanUsd(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `$${(abs / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(abs / 1_000).toFixed(0)}k`;
  return `$${abs.toFixed(0)}`;
}

export function stanceOf(p: PositioningIn): Stance {
  const total = p.notional_long + p.notional_short;
  if (total <= 0) return 'balanced';
  const skew = (p.notional_long - p.notional_short) / total;
  if (skew > DEAD_ZONE) return 'net_long';
  if (skew < -DEAD_ZONE) return 'net_short';
  return 'balanced';
}

export interface Verdict {
  stance: Stance;
  lowSample: boolean;
  headline: string;
  subtext: string;
}

export function verdict(asset: string, p: PositioningIn | null): Verdict {
  if (!p || (p.wallets_long + p.wallets_short) === 0) {
    return { stance: 'balanced', lowSample: true,
             headline: `No tracked-cohort positions in ${asset} under the current filters.`,
             subtext: '' };                                             // H5
  }
  const n = p.wallets_long + p.wallets_short;
  const st = stanceOf(p);
  if (n < MIN_SAMPLE) {
    return { stance: st, lowSample: true,
             headline: `Small sample: ${n} wallet${n === 1 ? '' : 's'} hold${n === 1 ? 's' : ''} ${asset} — read with care.`,  // H4
             subtext: `Small sample: ${n} wallets.` };                  // S4
  }
  const nl = humanUsd(p.notional_long);
  const ns = humanUsd(p.notional_short);
  let headline: string;
  if (st === 'net_long') headline = `Smart money is net long ${asset}: ${nl} long vs ${ns} short across ${n} wallets.`;        // H1
  else if (st === 'net_short') headline = `Smart money is net short ${asset}: ${ns} short vs ${nl} long across ${n} wallets.`; // H2
  else headline = `Smart money is split on ${asset}: ${nl} long vs ${ns} short across ${n} wallets.`;                          // H3

  const parts: string[] = [];
  // S1 — count-vs-notional divergence (both weightings must actually point
  // somewhere: sides nonzero and majorities strict)
  const countSide = p.wallets_long > p.wallets_short ? 'long'
    : p.wallets_short > p.wallets_long ? 'short' : null;
  const notionalSide = p.notional_long > p.notional_short ? 'long'
    : p.notional_short > p.notional_long ? 'short' : null;
  if (countSide && notionalSide && countSide !== notionalSide) {
    parts.push(`More wallets are ${countSide}, but whales drive the ${notionalSide} side.`);
  }
  // S2 — losing side's uPnL when material. "Losing side" = the side whose
  // uPnL is negative; if both are negative, the bigger loss.
  const sides: Array<['long' | 'short', number, number]> = [
    ['long', p.upnl_long, p.notional_long], ['short', p.upnl_short, p.notional_short]];
  const losers = sides.filter(([, u]) => u < 0).sort((a, b) => a[1] - b[1]);
  if (losers.length) {
    const [side, u, notional] = losers[0];
    if (Math.abs(u) >= UPNL_MATERIAL_ABS && notional > 0 && Math.abs(u) >= UPNL_MATERIAL_FRAC * notional) {
      parts.push(`${side === 'long' ? 'Longs' : 'Shorts'} are underwater ${humanUsd(u)} and holding.`);  // S2
    }
  }
  // S3 — fallback when weightings agree and no material pain
  if (!parts.length && countSide && notionalSide && countSide === notionalSide) {
    const pct = Math.round(100 * (countSide === 'long' ? p.wallets_long : p.wallets_short) / n);
    parts.push(`Count and dollar weighting agree — ${pct}% of positioned wallets are ${countSide}.`);    // S3
  }
  return { stance: st, lowSample: false, headline, subtext: parts.join(' ') };
}

interface Bucket { px_lo: number; px_hi: number; count: number; notional: number }

export function histogramTakeaway(longB: Bucket[], shortB: Bucket[],
                                  mark: number | null, lowSample: boolean): string {
  if (lowSample) return '';
  const parts: string[] = [];
  const nzLong = longB.filter((b) => b.count > 0);
  if (nzLong.length) {
    const modal = nzLong.reduce((a, b) => (b.notional > a.notional ? b : a));
    const mid = (modal.px_lo + modal.px_hi) / 2;
    let prof = '';
    if (mark != null) prof = mark > mid ? ' — in profit' : ' — underwater';
    parts.push(`Most longs entered near ${humanPx(mid)}${prof}.`);      // G1
  } else {
    parts.push('No long entries in this slice.');                        // G3
  }
  const nzShort = shortB.filter((b) => b.count > 0);
  if (nzShort.length) {
    const lo = Math.min(...nzShort.map((b) => b.px_lo));
    const hi = Math.max(...nzShort.map((b) => b.px_hi));
    parts.push(`Shorts entered ${humanPx(lo)}–${humanPx(hi)}.`);        // G2
  } else {
    parts.push('No short entries in this slice.');                       // G3
  }
  return parts.join(' ');
}

export function ageTakeaway(age: { h24: number; d1_7: number; d7_plus: number; undated: number }): string {
  const known = age.h24 + age.d1_7 + age.d7_plus;
  if (known === 0) return 'Position ages unknown for this slice.';       // A3
  if (age.h24 > 0) return `${age.h24} new entr${age.h24 === 1 ? 'y' : 'ies'} today — fresh conviction.`;  // A1
  return 'No fresh entries today — old positioning.';                    // A2
}

export function liqTakeaway(below: Bucket[], above: Bucket[]): string {
  const all = [...below.map((b) => ({ ...b, where: 'below' })),
               ...above.map((b) => ({ ...b, where: 'above' }))].filter((b) => b.count > 0);
  if (!all.length) return 'No cohort liquidation prices in this slice.';   // L2
  const top = all.reduce((a, b) => (b.notional > a.notional ? b : a));
  return `Largest liquidation pocket: ${humanUsd(top.notional)} at ` +
         `${humanPx(top.px_lo)}–${humanPx(top.px_hi)}, ${top.where} price.`; // L1
}

export function crowdingLine(c: { side: string; entry_lo: number; entry_hi: number;
                                  wallet_count: number; notional: number } | null,
                             minWallets: number, tolPct: number): string {
  if (!c) return `No crowded trades in this slice (needs ${minWallets}+ wallets within ±${tolPct}% entries).`;  // C2
  const mid = (c.entry_lo + c.entry_hi) / 2;
  return `${c.wallet_count} wallets crowded into the same ${c.side} near ${humanPx(mid)} ` +
         `(${humanUsd(c.notional)} combined).`;                             // C1
}

export function triggerSparseLine(withTriggers: number, checked: number): string {
  if (checked === 0) return 'No trigger observations yet — coverage builds as profiles are viewed.';  // T2
  return `Sparse — ${withTriggers} of ${checked} recently-checked wallets run resting TP/SL; ` +
         'most of this cohort manages exits without on-book triggers.';     // T1
}

export function freshConvictionStat(age: { h24: number; d1_7: number; d7_plus: number }): string | null {
  const dated = age.h24 + age.d1_7 + age.d7_plus;
  if (dated === 0) return null;
  return `Fresh conviction: ${Math.round((100 * age.h24) / dated)}% of dated positions opened in the last 24h.`;  // F1
}

interface LiqBucket extends Bucket { far?: boolean }

// SQ1 — squeeze implication for the largest NON-far cluster each side; falls
// back to L1/L2 (liqTakeaway) when only far outliers or nothing exists.
export function squeezeTakeaway(below: LiqBucket[], above: LiqBucket[]): string {
  const pick = (bs: LiqBucket[]) => {
    const near = bs.filter((b) => b.count > 0 && !b.far);
    return near.length ? near.reduce((a, b) => (b.notional > a.notional ? b : a)) : null;
  };
  const parts: string[] = [];
  const up = pick(above);
  if (up) parts.push(`A move to ${humanPx((up.px_lo + up.px_hi) / 2)} squeezes ${humanUsd(up.notional)} of shorts.`);
  const down = pick(below);
  if (down) parts.push(`A move to ${humanPx((down.px_lo + down.px_hi) / 2)} squeezes ${humanUsd(down.notional)} of longs.`);
  if (!parts.length) return liqTakeaway(below, above);
  return parts.join(' ');
}

// M1 — mover-card move summary
export function moverSummary(kind: string, asset: string, side: string, netDelta: number): string {
  const amt = humanUsd(netDelta);
  if (kind === 'grew') return `grew ${asset} ${side} +${amt}`;
  if (kind === 'cut') return `cut ${asset} ${side} −${amt}`;
  return `${kind} ${asset} ${side} · ${amt}`;   // flipped | opened | closed
}

// DV1/DV2/DV0 — divergence sentences (moved from backend f-strings per A8)
export function divergenceSentence(d: { asset: string; kind: string; cohort_side: string;
                                        price_change_24h?: number }): string {
  if (d.kind === 'funding') {
    return `${d.asset}: cohort net ${d.cohort_side} while funding pays the ${d.cohort_side}s — contrarian conviction.`;  // DV1
  }
  const chg = d.price_change_24h ?? 0;
  return `${d.asset}: price ${chg > 0 ? '+' : ''}${chg.toFixed(1)}% in 24h against a net-${d.cohort_side} cohort — ` +
         (d.cohort_side === 'long' ? 'accumulation into weakness.' : 'distribution into strength.');  // DV2
}

export function divergenceNone(): string {
  return 'No notable divergences right now.';                             // DV0
}

// CV1/CV2 — conviction badge label (owner decision 2026-08-26: MARGIN-based —
// margin = notional ÷ leverage, shown as share of account value; display
// capped at 999%. MM variant contextualizes, never hides.)
export const CONVICTION_DISPLAY_CAP = 999;
export function convictionLabel(pct: number, isMm: boolean): string {
  const shown = pct > CONVICTION_DISPLAY_CAP
    ? `${CONVICTION_DISPLAY_CAP}%+` : `${pct.toFixed(0)}%`;
  return isMm ? `${shown} margin — routine for this book.`                // CV2
              : `uses ${shown} of account as margin`;                     // CV1
}

// TL1/TL2 — pulse tile caption
export function tileCaption(stance: string, netNotional: number, cohortOi: number): string {
  if (stance === 'balanced') return `balanced · OI ${humanUsd(cohortOi)}`;                    // TL2
  return `net ${stance === 'net_long' ? 'long' : 'short'} ${humanUsd(netNotional)} · OI ${humanUsd(cohortOi)}`;  // TL1
}

// R1 — risk-rotation read
export function rotationRead(now: number, start: number): string {
  return now >= start ? 'de-risking toward majors' : 'rotating into alts';
}

// EE1 — entrants/exits empty state
export function emptyChurn(kind: 'entrants' | 'exits'): string {
  return `No ${kind} in 24h — low churn.`;
}

// TR0 — SMI track-record collecting state (Part A4). The SERVER withholds
// study numbers below the gate; this sentence only narrates the counters it
// returns. The threshold text comes from the server payload, never hardcoded.
export function trackRecordCollecting(n: number, days: number,
                                      minN: number, minDays: number): string {
  const d = days < 10 ? days.toFixed(1) : days.toFixed(0);   // never round 4.6d up to "5 days"
  return `Track record: collecting — ${n} observation${n === 1 ? '' : 's'} over ` +
         `${d} day${d === '1.0' ? '' : 's'}; ` +
         `publishes at ${minN} obs / ${minDays} days.`;
}

// TR1 — one published bucket/horizon line
const TR_BUCKET_LABEL: Record<string, string> = {
  '0_30': '0–30 (bearish)', '30_45': '30–45', '45_55': '45–55 (neutral)',
  '55_70': '55–70', '70_100': '70–100 (bullish)',
  drop_big: 'dropped ≥10', drop: 'dropped 3–10', flat: 'flat ±3',
  rise: 'rose 3–10', rise_big: 'rose ≥10',
};
export function trackRecordLine(asset: string, kind: string, bucket: string,
                                horizon: string, medianRet: number,
                                hitRate: number | null, n: number): string {
  const b = TR_BUCKET_LABEL[bucket] ?? bucket;
  const what = kind === 'smi_change_24h' ? `SMI ${b} in 24h` : `SMI read ${b}`;
  const med = `${medianRet >= 0 ? '+' : ''}${(medianRet * 100).toFixed(2)}%`;
  const hit = hitRate != null ? `hit ${(hitRate * 100).toFixed(0)}%, ` : '';
  return `When ${what}, ${asset} moved ${med} median over ${horizon} (${hit}n=${n}).`;
}

// CTX1/CTX2/CTX3/CTX0 — copy-modal smart-money context (Tier-2 Part B2).
// Three lines max; a DISPLAY block only — these sentences gate nothing.
export function ctxStanceLine(asset: string, stance: Stance,
                              smi: number | null, calibrating: boolean): string {
  const smiPart = smi != null
    ? ` (SMI ${smi.toFixed(0)}${calibrating ? ' · calibrating' : ''})` : '';
  if (stance === 'balanced') return `Smart money is split on ${asset}${smiPart}`;
  return `Smart money is net ${stance === 'net_long' ? 'LONG' : 'SHORT'} ${asset}${smiPart}`;   // CTX1
}

export function ctxCrowdLine(userSide: 'long' | 'short',
                             crowd: { side: string; wallet_count: number;
                                      entry_lo: number; entry_hi: number }): string {
  const mid = humanPx((crowd.entry_lo + crowd.entry_hi) / 2);
  return `your trade is ${userSide === crowd.side ? 'WITH' : 'AGAINST'} a ` +
         `${crowd.wallet_count}-wallet crowd near ${mid}`;                    // CTX2
}

// HL convention: positive hourly funding = longs pay shorts. "HL" is stated —
// the modal's execution preview separately shows the PERPL funding the user
// actually pays; conflating the two venues would be dishonest.
export function ctxFundingLine(userSide: 'long' | 'short',
                               fundingHourly: number): string {
  const pays = userSide === 'long' ? fundingHourly < 0 : fundingHourly > 0;
  return `HL funding ${pays ? 'pays' : 'costs'} your side ` +
         `${(Math.abs(fundingHourly) * 100).toFixed(4)}%/h`;                  // CTX3
}

export function ctxUnavailable(): string {
  return 'Smart money context unavailable.';                                  // CTX0
}

// CP1 — asset page → Discover reverse link (Perpl-mapped assets only)
export function copyableLine(n: number): string {
  return `Copyable on Perpl · ${n} leader${n === 1 ? '' : 's'} hold${n === 1 ? 's' : ''} this position`;
}

// WD1 — three-weighting divergence (Tier-2 C3): one sentence comparing the
// long share under $ / count / quality weighting. null when quality has no
// weighted wallets (nothing honest to compare).
export function weightingDivergence(dollarPct: number, countPct: number,
                                    qualityPct: number | null): string | null {
  if (qualityPct == null) return null;
  const spread = Math.max(dollarPct, countPct, qualityPct)
    - Math.min(dollarPct, countPct, qualityPct);
  const base = `Long share: ${dollarPct.toFixed(0)}% by dollars, ` +
    `${countPct.toFixed(0)}% by count, ${qualityPct.toFixed(0)}% by quality`;
  if (spread < 5) return `${base} — all three weightings agree.`;
  const modes: Array<[string, number]> = [
    ['dollars', dollarPct], ['count', countPct], ['quality', qualityPct]];
  modes.sort((a, b) => a[1] - b[1]);
  return `${base} — ${modes[2][0]} lean${modes[2][0] === 'dollars' ? '' : 's'} ` +
    `longest, ${modes[0][0]} most short.`;
}

// QS1 — compact quality-stat line (Tier-2 D2). Win rate NEVER renders
// without PF beside it once PF exists — this function is the enforcement.
export function qualityStatLine(winRate: number | null, trades: number | null,
                                pf: number | null, ddPct: number | null,
                                ddUsd: number | null): string | null {
  const parts: string[] = [];
  if (winRate != null) parts.push(`${(winRate * 100).toFixed(0)}%${trades != null ? ` (${trades} trades)` : ''}`);
  if (pf != null) parts.push(`PF ${pf >= 999 ? '∞' : pf.toFixed(1)}`);
  if (ddPct != null) parts.push(`maxDD ${(ddPct * 100).toFixed(1)}%`);
  else if (ddUsd != null && ddUsd > 0) parts.push(`maxDD ${humanUsd(ddUsd)}`);
  return parts.length ? parts.join(' · ') : null;
}

// FA1 — flip-accuracy rating (Tier-2 D3): rated only at n >= 10
export function flipRating(acc: number | null, n: number | null): string {
  if (acc != null && (n ?? 0) >= 10) return `flip acc ${(acc * 100).toFixed(0)}% (n=${n})`;
  return '(unrated)';
}

export function venueSentence(voiUsd: number | null, cohortOi: number,
                              fundingHourly: number | null): string {
  if (voiUsd == null || voiUsd <= 0) return 'Venue context unavailable this cycle.';  // V2
  const share = ((cohortOi / voiUsd) * 100).toFixed(1);
  const f = fundingHourly != null ? ` · funding ${(fundingHourly * 100).toFixed(4)}%/h` : '';
  return `Venue OI ${humanUsd(voiUsd)} — this cohort holds ${share}% of it${f}.`;     // V1
}
