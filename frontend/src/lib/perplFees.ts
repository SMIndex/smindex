/**
 * Perpl fee model — single source of truth for trader-facing fee estimates.
 *
 * RAW SOURCE (authoritative): Perpl public context `/api/v1/pub/context`,
 *   markets[].config.maker_fee = 90, taker_fee = 690 (2026-08 schedule; the values
 *   are live — never hardcode them, the backend re-reads context on every call).
 *
 * UNIT: hundredths of a basis point.  raw / 100 = bps  ;  raw / 1_000_000 = fraction.
 *   Backend `/api/market-configs` (app/main.py) already applies `raw / 100`, so the
 *   values that reach the frontend config (`takerFeeBps`, `makerFeeBps`) are ALREADY
 *   in bps:  takerFeeBps = 6.9,  makerFeeBps = 0.9.
 *
 * DOCS (https://docs.perpl.xyz/exchange/fees.md) — 7-tier schedule in BPS, Tier 1
 *   (front-end trades are charged Tier 1 up front):
 *     T1: Maker Open 0.9  ·  Taker Open 6.9  ·  Close = 0 for both, all tiers
 *   => Fees are charged ONLY on the OPEN side of a trade; closing costs 0.
 *   => Volume-tier discounts (T2..VIP2) are paid back as BIWEEKLY REBATES off-chain,
 *      so the up-front charge is always Tier 1 regardless of the connected wallet.
 *
 * MAKER vs TAKER in this app (see lib/perplTrading.ts):
 *   - Market order = IOC (flags = 4) -> taker -> 6.9 bps.
 *   - Limit order default = GTC (flags = 0) -> may fill as MAKER or TAKER; the
 *     honest display is the worst-case taker estimate.
 *   - Limit order with the Post-Only toggle ON = fl:1 -> guaranteed maker
 *     (0.9 bps) but rejects if it would cross the book on entry.
 */

export type FeeKind = 'taker' | 'maker' | 'estimate';

export interface FeeRateSource {
  takerFeeBps: number;
  makerFeeBps?: number;
}

export interface OrderFeeRate {
  bps: number;
  kind: FeeKind;
}

/** Perpl charges no fee on the closing side of a trade (Taker Close = Maker Close = 0). */
export const PERPL_CLOSE_FEE_BPS = 0;

/**
 * Normalize a fee value to BPS, defensively.
 *
 * The backend `/api/market-configs` is supposed to divide the raw context value
 * (`taker_fee=690`, `maker_fee=90`) by 100 so the frontend receives bps (6.9, 0.9).
 * If an unscaled raw value ever slips through (e.g. a stale backend process started
 * before that `/100` was added, serving the raw `880`), the frontend would otherwise
 * render 6.9% instead of 6.9 bps — a 100x fee error.
 *
 * Anchored to the documented model, NOT a guess: Perpl's on-chain max trading fee is
 * 10% = 1000 bps and the real taker/maker rates are single-digit bps (8.8 / 5). No real
 * per-trade fee reaches 100 bps (1%), while the raw encoding is always >= 100 (90 is the one sub-100 raw; 0.9 bps passes through either way).
 * So a value >= 100 can only be raw hundredths-of-a-bps and is divided by 100; a value
 * below that is already bps and passes through unchanged (6.9 -> 6.9, 690 -> 6.9).
 */
export function normalizePerplFeeBps(value: number | null | undefined): number {
  if (value == null || !Number.isFinite(value) || value <= 0) return 0;
  return value >= 100 ? value / 100 : value;
}

/**
 * Pick the correct trader-facing fee rate for an order.
 * @param isLimit   true for limit orders.
 * @param postOnly  whether the limit is actually sent PostOnly (fl:1, guaranteed
 *                  maker). Defaults to FALSE — this app sends limit orders GTC
 *                  (fl:0) unless the user enables the Post-Only toggle, and a GTC
 *                  limit that crosses pays taker, so the honest default display is
 *                  the worst-case taker estimate.
 */
export function getOrderFeeRate(
  cfg: FeeRateSource,
  { isLimit, postOnly = false }: { isLimit: boolean; postOnly?: boolean },
): OrderFeeRate {
  const takerBps = normalizePerplFeeBps(cfg.takerFeeBps);
  const makerBps = cfg.makerFeeBps != null ? normalizePerplFeeBps(cfg.makerFeeBps) : undefined;
  if (!isLimit) return { bps: takerBps, kind: 'taker' };
  if (postOnly && makerBps != null) return { bps: makerBps, kind: 'maker' };
  // Non-PostOnly limit could cross and pay taker -> show the worst case as an estimate.
  return { bps: takerBps, kind: 'estimate' };
}

/** Fee in USD for a given notional (USD) at a bps rate. */
export function calculateEstimatedFee(notionalUsd: number, rateBps: number): number {
  if (!Number.isFinite(notionalUsd) || !Number.isFinite(rateBps)) return 0;
  return notionalUsd * (rateBps / 10_000);
}
