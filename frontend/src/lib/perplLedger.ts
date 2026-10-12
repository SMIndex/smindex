/**
 * Account ledger derived from PERPL'S OWN FILLS (2026-10-08).
 *
 * Everything here is computed from the documented fill fields only,
 * `at, mkt, acc, oid, t, l, p, s, f, bfa` (docs: api/rest, GET
 * /v1/trading/fills), so no field name is guessed:
 *
 *   t   1 OpenLong / 2 OpenShort / 3 CloseLong / 4 CloseShort
 *   l   1 maker / 2 taker
 *   p   price scaled by the market's price_decimals
 *   s   size  scaled by the market's size_decimals
 *   f   fee in micro-USD (negative = rebate)
 *
 * Realized P/L is matched FIFO per market: every close fill is matched against
 * the oldest unmatched open fills at their own prices. That is the same thing
 * the venue does for a position, and it means the realized number, the win
 * rate, the daily P/L calendar and the equity curve all come from real fills
 * rather than from our 4-hourly snapshots or from estimates.
 */
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { atMs } from '@/lib/perplAccountStats';
import type { PerplFill } from '@/lib/perplRest';

export interface LedgerTrade {
  closedAt: number;
  openedAt: number;
  marketId: number;
  side: 'long' | 'short';
  size: number;
  entryPrice: number;
  exitPrice: number;
  grossPnl: number;
  fees: number;
  netPnl: number;
  closeOid: number;
  openOids: number[];
}

export interface LedgerTotals {
  trades: number;
  wins: number;
  losses: number;
  winRatePct: number;
  volumeUsd: number;
  feesUsd: number;
  grossPnl: number;
  netPnl: number;
}

const MICRO = 1e6;

function scale(marketId: number): { pd: number; sd: number } | null {
  const c = MARKET_CONFIGS[marketId];
  // No guessed decimals: a market we have no settings for is left out of the
  // maths and reported separately, never scaled by a wrong power of ten.
  if (!c || c.priceDecimals == null || c.sizeDecimals == null) return null;
  return { pd: 10 ** c.priceDecimals, sd: 10 ** c.sizeDecimals };
}

export interface LedgerResult {
  trades: LedgerTrade[];
  totals: LedgerTotals;
  /** fills we could not use, by reason */
  skipped: { unknownMarket: number; unmatchedClose: number };
  openLots: Array<{ marketId: number; side: 'long' | 'short'; size: number; price: number; at: number }>;
  feesUsdAllFills: number;
  volumeUsdAllFills: number;
  fillCount: number;
  firstFillAt: number | null;
  lastFillAt: number | null;
}

/** FIFO-match fills into closed trades. Input may be in any time order. */
export function buildLedger(fills: PerplFill[]): LedgerResult {
  // `at` is an OBJECT ({b, t, tx, txid, l}); `at.t` is the ms timestamp. Sorting
  // and dating on `at` itself produced NaN for every row.
  const rows = [...fills].map((f) => ({ ...f, _t: atMs(f) })).sort((a, b) => a._t - b._t);
  const lots = new Map<string, Array<{ size: number; price: number; at: number; oid: number }>>();
  const trades: LedgerTrade[] = [];
  const skipped = { unknownMarket: 0, unmatchedClose: 0 };
  let feesUsdAllFills = 0;
  let volumeUsdAllFills = 0;
  let firstFillAt: number | null = null;
  let lastFillAt: number | null = null;

  for (const f of rows) {
    const sc = scale(f.mkt);
    if (!sc) { skipped.unknownMarket++; continue; }
    const price = f.p / sc.pd;
    const size = f.s / sc.sd;
    const fee = Number(f.f ?? 0) / MICRO;
    if (!(size > 0)) continue;
    feesUsdAllFills += fee;
    volumeUsdAllFills += price * size;
    if (firstFillAt == null) firstFillAt = f._t;
    lastFillAt = f._t;

    const isOpen = f.t === 1 || f.t === 2;
    const side: 'long' | 'short' = (f.t === 1 || f.t === 3) ? 'long' : 'short';
    const key = `${f.mkt}:${side}`;

    if (isOpen) {
      const arr = lots.get(key) ?? [];
      arr.push({ size, price, at: f._t, oid: f.oid });
      lots.set(key, arr);
      continue;
    }

    // close fill: consume the oldest open lots of the same market+side
    let remaining = size;
    const arr = lots.get(key) ?? [];
    let gross = 0;
    let entryNotional = 0;
    let matched = 0;
    const openOids: number[] = [];
    let openedAt = f._t;
    while (remaining > 1e-12 && arr.length) {
      const lot = arr[0];
      const take = Math.min(lot.size, remaining);
      gross += side === 'long' ? (price - lot.price) * take : (lot.price - price) * take;
      entryNotional += lot.price * take;
      matched += take;
      openOids.push(lot.oid);
      openedAt = Math.min(openedAt, lot.at);
      lot.size -= take;
      remaining -= take;
      if (lot.size <= 1e-12) arr.shift();
    }
    lots.set(key, arr);
    if (matched <= 1e-12) { skipped.unmatchedClose++; continue; }
    // Fees: this close fill's fee, plus the open fees are already counted in
    // feesUsdAllFills: per-trade we attribute the close fee only, and report
    // total fees separately, so nothing is double counted in netPnl totals.
    trades.push({
      closedAt: f._t, openedAt, marketId: f.mkt, side, size: matched,
      entryPrice: matched > 0 ? entryNotional / matched : price,
      exitPrice: price, grossPnl: gross, fees: fee, netPnl: gross - fee,
      closeOid: f.oid, openOids: [...new Set(openOids)],
    });
    if (remaining > 1e-12) skipped.unmatchedClose++;
  }

  const totals = totalsFor(trades);
  const openLots: LedgerResult['openLots'] = [];
  lots.forEach((arr, key) => {
    const [mkt, side] = key.split(':');
    arr.forEach((l) => openLots.push({
      marketId: Number(mkt), side: side as 'long' | 'short', size: l.size, price: l.price, at: l.at,
    }));
  });

  return {
    trades: trades.sort((a, b) => b.closedAt - a.closedAt),
    totals, skipped, openLots,
    feesUsdAllFills, volumeUsdAllFills,
    fillCount: rows.length, firstFillAt, lastFillAt,
  };
}

export function totalsFor(trades: LedgerTrade[]): LedgerTotals {
  const wins = trades.filter((t) => t.netPnl > 0).length;
  const losses = trades.filter((t) => t.netPnl < 0).length;
  return {
    trades: trades.length,
    wins,
    losses,
    winRatePct: trades.length ? (wins / trades.length) * 100 : 0,
    volumeUsd: trades.reduce((a, t) => a + t.exitPrice * t.size, 0),
    feesUsd: trades.reduce((a, t) => a + t.fees, 0),
    grossPnl: trades.reduce((a, t) => a + t.grossPnl, 0),
    netPnl: trades.reduce((a, t) => a + t.netPnl, 0),
  };
}

/** Net realized P/L per UTC day, from the matched trades. */
export function dailyPnl(trades: LedgerTrade[]): Map<string, { pnl: number; trades: number; volume: number }> {
  const out = new Map<string, { pnl: number; trades: number; volume: number }>();
  for (const t of trades) {
    const day = new Date(t.closedAt).toISOString().slice(0, 10);
    const cur = out.get(day) ?? { pnl: 0, trades: 0, volume: 0 };
    cur.pnl += t.netPnl;
    cur.trades += 1;
    cur.volume += t.exitPrice * t.size;
    out.set(day, cur);
  }
  return out;
}

export function sinceMs(days: number): number {
  return Date.now() - days * 86400_000;
}

export function realizedSince(trades: LedgerTrade[], fromMs: number): number {
  return trades.filter((t) => t.closedAt >= fromMs).reduce((a, t) => a + t.netPnl, 0);
}

/** Today in UTC, 00:00. */
export function startOfUtcDay(): number {
  const d = new Date();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

// ---------------------------------------------------------------------------
// Account events (deposits / withdrawals / funding / settlements / liquidation)
// ---------------------------------------------------------------------------

/**
 * The documented AccountEvent fields are `at, in, id, et, m, r, o, p, a, b,
 * lb, f, bfa`, and the `et` enum IS published after all (api/rest,
 * AccountEventType): 1 Deposit, 2 Withdrawal, 3 IncreasePositionCollateral,
 * 4 Settlement, 5 Liquidation, 6 TransferToProtocol, 7 TransferFromProtocol,
 * 8 Funding, 9 Deleveraging, 10 Unwinding, 11 PositionCollateralDecreased,
 * 12 LastForwardedDescIdReset. The named map lives in perplAccountStats.
 */
export interface LedgerEvent {
  at: number;
  et: number;
  amount: number;
  balanceAfter: number | null;
  lockedAfter: number | null;
  marketId: number | null;
  fee: number;
  kind: 'transfer' | 'market' | 'unknown';
}

export function buildEvents(raw: any[]): { events: LedgerEvent[]; byType: Map<number, number> } {
  const events: LedgerEvent[] = [];
  const byType = new Map<number, number>();
  for (const e of raw ?? []) {
    if (e == null || typeof e !== 'object') continue;
    const amount = Number(e.a ?? 0) / MICRO;
    const ev: LedgerEvent = {
      at: atMs(e),
      et: e.et ?? -1,
      amount,
      balanceAfter: e.b != null ? Number(e.b) / MICRO : null,
      lockedAfter: e.lb != null ? Number(e.lb) / MICRO : null,
      marketId: e.m ?? null,
      fee: Number(e.f ?? 0) / MICRO,
      kind: e.m != null ? 'market' : (e.a != null ? 'transfer' : 'unknown'),
    };
    events.push(ev);
    byType.set(ev.et, (byType.get(ev.et) ?? 0) + 1);
  }
  events.sort((a, b) => b.at - a.at);
  return { events, byType };
}

/** Equity series from the balance recorded after each account event. */
export function equityFromEvents(events: LedgerEvent[]): Array<{ t: number; equity: number }> {
  return events
    .filter((e) => e.balanceAfter != null)
    .map((e) => ({ t: Math.floor(e.at / 1000), equity: (e.balanceAfter ?? 0) + (e.lockedAfter ?? 0) }))
    .sort((a, b) => a.t - b.t);
}
