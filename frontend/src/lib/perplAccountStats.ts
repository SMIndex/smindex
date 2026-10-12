/**
 * Everything the Portfolio shows, derived from Perpl's OWN account records.
 *
 * Built 2026-10-09 from frames and REST pages captured read-only from Perpl for
 * the owner's account 161 (audits/SYNC_UI_REPORT.md section 4). No field name
 * and no scale in this file is a guess, each one appears in a captured row.
 *
 * Feeds and what each is authoritative for
 * ----------------------------------------
 *  mt:19 `sts[]`              LIFETIME totals: volume, fees, realized P/L,
 *                             deposits, withdrawals, trade count, win rate.
 *                             One frame, no paging, complete by construction.
 *  /v1/trading/position-history  The venue's own per-trade record: entry, exit,
 *                             size, realized P/L, funding, fees, leverage.
 *                             This is the trade list, not FIFO guesswork.
 *  /v1/trading/fills          Per-execution detail: price, size, fee, maker or
 *                             taker. Used for volume and for hold time.
 *  /v1/trading/account-history  Balance-affecting events: deposits (et 1),
 *                             withdrawals (et 2), settlements (et 4),
 *                             liquidations (et 5), funding (et 8).
 *
 * Shapes, captured verbatim
 * -------------------------
 *  at    {b, t, tx, txid, l}  -- an OBJECT. `t` is the ms timestamp.
 *  Position  {mkt, acc, pid, rq, oid, st, sr, sd, c, ep, epr, s, fee, cfee,
 *             efs, lv, cpnl, dpnl, fnd, pay, xp, xfs, ots}
 *  Fill      {at, mkt, acc, oid, t, l, p, s, f, bfa}
 *  Event     {at, in, id, et, m, r, o, p, a, b, lb, f, bfa}
 *
 * Scales: money fields are micro-USD (1e6); prices use the market's
 * price_decimals; sizes use its size_decimals; `lv` is leverage in hundredths.
 * All three proven in the report's section 1 cross-check.
 */
import { MARKETS } from '@/config/constants';
import { MARKET_CONFIGS } from '@/lib/perplTrading';

const MICRO = 1e6;

const n = (v: any): number => {
  const x = typeof v === 'string' ? Number(v) : v;
  return Number.isFinite(x) ? x : 0;
};
const usd = (v: any): number => n(v) / MICRO;

/** Every timestamp on these feeds is `at.t`, inside an object. */
export const atMs = (row: any): number => n(row?.at?.t ?? row?.at);

function scales(marketId: number): { pd: number; sd: number } | null {
  const c = MARKET_CONFIGS[marketId];
  if (!c || c.priceDecimals == null || c.sizeDecimals == null) return null;
  return { pd: 10 ** c.priceDecimals, sd: 10 ** c.sizeDecimals };
}

export function marketSymbol(marketId: number): string {
  return MARKETS[marketId]?.symbol ?? `M${marketId}`;
}

// ---------------------------------------------------------------------------
// Account events
// ---------------------------------------------------------------------------

/**
 * AccountEventType, from Perpl's REST docs (api/rest, "AccountEventType").
 * Only the codes the docs list, nothing invented. Account 161's own history
 * contains et 4 (settlement) and et 7 (transfer from protocol); the rest are
 * labelled for when they appear.
 */
export const EVENT_TYPE: Record<number, string> = {
  1: 'Deposit',
  2: 'Withdrawal',
  3: 'Position collateral increased',
  4: 'Trade settlement',
  5: 'Liquidation',
  6: 'Transfer to protocol',
  7: 'Transfer from protocol',
  8: 'Funding',
  9: 'Deleveraging',
  10: 'Unwinding',
  11: 'Position collateral decreased',
  12: 'Forwarded request id reset',
};

export interface AccountEvent {
  at: number;
  et: number;
  label: string;
  amount: number;          // signed USD
  balanceAfter: number | null;
  lockedAfter: number | null;
  marketId: number | null;
  fee: number;
  txid: string | null;
}

export function buildAccountEvents(rows: any[]): AccountEvent[] {
  return (rows ?? [])
    .filter((e) => e && typeof e === 'object')
    .map((e): AccountEvent => ({
      at: atMs(e),
      et: n(e.et),
      label: EVENT_TYPE[n(e.et)] ?? `Event ${e.et}`,
      amount: usd(e.a),
      balanceAfter: e.b != null ? usd(e.b) : null,
      lockedAfter: e.lb != null ? usd(e.lb) : null,
      marketId: e.m != null ? n(e.m) : null,
      fee: usd(e.f),
      txid: e?.at?.txid && !/^0+$/.test(e.at.txid) ? e.at.txid : null,
    }))
    .sort((a, b) => b.at - a.at);
}

export interface TransferTotals {
  deposits: number;
  withdrawals: number;
  netDeposited: number;
  liquidations: number;        // count
  fundingFromEvents: number;   // signed; only et 8 rows
  covers: { from: number | null; to: number | null; rows: number };
}

export function transferTotals(events: AccountEvent[]): TransferTotals {
  const sum = (et: number) => events.filter((e) => e.et === et).reduce((a, e) => a + e.amount, 0);
  const deposits = sum(1) + sum(7);        // on-ramp + transfer in from the protocol
  const withdrawals = Math.abs(sum(2)) + Math.abs(sum(6));
  const times = events.map((e) => e.at).filter((t) => t > 0);
  return {
    deposits,
    withdrawals,
    netDeposited: deposits - withdrawals,
    liquidations: events.filter((e) => e.et === 5).length,
    fundingFromEvents: sum(8),
    covers: {
      from: times.length ? Math.min(...times) : null,
      to: times.length ? Math.max(...times) : null,
      rows: events.length,
    },
  };
}

/** Equity after each balance-affecting event (balance + locked). */
export function equityCurve(events: AccountEvent[]): Array<{ t: number; equity: number }> {
  return events
    .filter((e) => e.balanceAfter != null)
    .map((e) => ({ t: e.at, equity: (e.balanceAfter ?? 0) + (e.lockedAfter ?? 0) }))
    .sort((a, b) => a.t - b.t);
}

// ---------------------------------------------------------------------------
// Closed trades: the venue's own position records
// ---------------------------------------------------------------------------

export interface ClosedTrade {
  at: number;              // when it closed
  pid: number;
  marketId: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entryPrice: number;
  exitPrice: number | null;
  realizedPnl: number;     // dpnl: the venue's own realized P/L for the position
  funding: number;         // fnd
  fees: number;            // fee (already includes the close fee, cfee)
  leverage: number;        // lv/100
  collateral: number;
  volume: number;          // |size| * entry, in USD
  netPnl: number;          // realized + funding - fees
  openedAt: number | null; // from the matching fills, when we have them
}

/**
 * position-history rows -> ClosedTrade[].
 *
 * `sd` is the venue's PositionType: 1 Long, 2 Short. (The on-chain enum is
 * 0/1: a different scale, which is exactly why this is read off a captured
 * row and not shared with the chain reader.)
 *
 * IMPORTANT: position-history is an EVENT LOG, not one row per trade. The
 * owner's own account returns 24,651 rows for 10,276 positions, `st: 1` rows
 * restate a position each time it grows, and exactly one `st: 2` row closes
 * it. Each row's `fee` is the RUNNING total for that position, so summing
 * every row double-counts: over the full history, all rows give $828.94 of
 * fees where the venue's own lifetime figure is $414.47, while the `st: 2`
 * rows alone give $402.84. Only the closing row is a trade.
 */
const POSITION_CLOSED_ST = 2;

export function buildClosedTrades(rows: any[]): ClosedTrade[] {
  const out: ClosedTrade[] = [];
  const seen = new Set<number>();
  // When each position FIRST appeared, over every row for that pid, this is
  // the open time, so hold time needs no inference.
  //
  // Rejected alternative, for the record: the row's `rq`. It looks like an ms
  // epoch on some rows, but it is the closing ORDER's request id, which the
  // client that placed it chose, one of the owner's rows carries rq 59, which
  // decodes to 1970, and rq-derived holds agreed with the position's own first
  // row on 1 of 2,987 trades. A field that is right by coincidence is not a
  // source.
  const openedAt = new Map<number, number>();
  for (const r of rows ?? []) {
    if (!r || r.pid == null) continue;
    const pid = n(r.pid);
    const t = atMs(r);
    if (!t) continue;
    const prev = openedAt.get(pid);
    if (prev == null || t < prev) openedAt.set(pid, t);
  }
  // CORRECTION (2026-10-12): a position can be closed in PIECES. Every row
  // that carries an exit price (`xp`) - the `st: 1, sr: 14` reduces and the
  // final `st: 2` row - describes ONLY the piece it closed: its own `s`,
  // `dpnl`, `fnd`, `c` and `fee`. Reading the `st: 2` row alone turned the
  // owner's auto-copy PUMP position (1,135 pieces, 11,167 PUMP, +$2.66) into
  // "1 PUMP, $0.01 volume, +$0.00". The pieces are summed here.
  //
  // `fee` on those rows is already the piece's TOTAL fee (its share of the
  // entry fees plus its close fee); `cfee` is the close part of it, not an
  // extra. Proven on the same account: summed `fee` = $10.2178 against
  // $10.2088 of fees on Perpl's own fills for the same window, where
  // `fee + cfee` gave $14.15.
  const pieces = new Map<number, { size: number; exitNotional: number; entryNotional: number;
    pnl: number; funding: number; fees: number; collateral: number }>();
  for (const r of rows ?? []) {
    if (!r || r.pid == null || r.mkt == null || r.xp == null) continue;
    const sc = scales(n(r.mkt));
    if (!sc) continue;
    const pid = n(r.pid);
    const p = pieces.get(pid) ?? { size: 0, exitNotional: 0, entryNotional: 0, pnl: 0, funding: 0, fees: 0, collateral: 0 };
    const s = n(r.s) / sc.sd;
    p.size += s;
    p.exitNotional += s * (n(r.xp) / sc.pd);
    p.entryNotional += s * (n(r.ep) / sc.pd);
    p.pnl += usd(r.dpnl);
    p.funding += usd(r.fnd);
    p.fees += usd(r.fee);
    p.collateral += Math.abs(usd(r.c));
    pieces.set(pid, p);
  }
  for (const r of rows ?? []) {
    if (!r || r.pid == null || r.mkt == null) continue;
    if (n(r.st) !== POSITION_CLOSED_ST) continue;      // only a closed position is a trade
    if (seen.has(n(r.pid))) continue;                  // newest closing row wins
    seen.add(n(r.pid));
    const p = pieces.get(n(r.pid));
    if (!p || !(p.size > 0)) continue;    // never scale a market we have no settings for
    out.push({
      at: atMs(r),
      pid: n(r.pid),
      marketId: n(r.mkt),
      symbol: marketSymbol(n(r.mkt)),
      side: n(r.sd) === 2 ? 'short' : 'long',
      size: p.size,
      entryPrice: p.entryNotional / p.size,
      exitPrice: p.exitNotional / p.size,
      realizedPnl: p.pnl,
      funding: p.funding,
      fees: p.fees,
      leverage: n(r.lv) / 100,
      collateral: p.collateral,
      volume: p.entryNotional,
      netPnl: p.pnl + p.funding - p.fees,
      openedAt: openedAt.get(n(r.pid)) ?? null,
    });
  }
  return out.sort((a, b) => b.at - a.at);
}

/**
 * Kept as the identity function: `openedAt` now comes from the position's own
 * first history row inside buildClosedTrades, which is exact. Pairing close
 * fills with open fills by market and side could not be exact, on a market
 * with ten thousand interleaved trades the pairing drifts, and it reported a
 * 4.8-day average hold for positions the owner's bot held for seconds.
 */
export function attachHoldTimes(trades: ClosedTrade[], _fills: any[]): ClosedTrade[] {
  return trades;
}

export interface TradeStats {
  trades: number;
  wins: number;
  losses: number;
  /** Net of this position's own fees and funding, "did it make money". */
  winRatePct: number | null;
  /**
   * On realized P/L alone, before the position's fees.
   *
   * Both are reported because they differ a lot and the difference is the
   * point: on the owner's full history 11.99% of closed positions had a
   * positive realized P/L while only 1.20% were still positive after fees.
   * Perpl's own `wr` (16.35%) is a third number again, a per-trade figure
   * over its own 8,741 trade count, so the Portfolio shows Perpl's figure as
   * Perpl's and ours as ours instead of pretending one is the other.
   */
  winRateGrossPct: number | null;
  volume: number;
  fees: number;
  funding: number;
  realizedPnl: number;
  netPnl: number;
  best: ClosedTrade | null;
  worst: ClosedTrade | null;
  avgLeverage: number | null;
  avgHoldMs: number | null;
  avgTrade: number | null;
}

export function tradeStats(trades: ClosedTrade[]): TradeStats {
  // A position opened and closed inside one block has openedAt === at: a real
  // zero-length hold, kept (>= rather than >) so fast trades are not dropped.
  const held = trades.filter((t) => t.openedAt != null && t.at >= t.openedAt!);
  const wins = trades.filter((t) => t.netPnl > 0).length;
  const losses = trades.filter((t) => t.netPnl < 0).length;
  const grossWins = trades.filter((t) => t.realizedPnl > 0).length;
  const sorted = [...trades].sort((a, b) => b.netPnl - a.netPnl);
  return {
    trades: trades.length,
    wins,
    losses,
    winRatePct: trades.length ? (wins / trades.length) * 100 : null,
    winRateGrossPct: trades.length ? (grossWins / trades.length) * 100 : null,
    volume: trades.reduce((a, t) => a + t.volume, 0),
    fees: trades.reduce((a, t) => a + t.fees, 0),
    funding: trades.reduce((a, t) => a + t.funding, 0),
    realizedPnl: trades.reduce((a, t) => a + t.realizedPnl, 0),
    netPnl: trades.reduce((a, t) => a + t.netPnl, 0),
    best: sorted[0] ?? null,
    worst: sorted.length > 1 ? sorted[sorted.length - 1] : null,
    avgLeverage: trades.length
      ? trades.reduce((a, t) => a + t.leverage, 0) / trades.length : null,
    avgHoldMs: held.length
      ? held.reduce((a, t) => a + (t.at - (t.openedAt ?? t.at)), 0) / held.length : null,
    avgTrade: trades.length ? trades.reduce((a, t) => a + t.netPnl, 0) / trades.length : null,
  };
}

export interface MarketStats {
  marketId: number;
  symbol: string;
  trades: number;
  volume: number;
  netPnl: number;
  fees: number;
  winRatePct: number | null;
}

export function byMarket(trades: ClosedTrade[]): MarketStats[] {
  const m = new Map<number, ClosedTrade[]>();
  for (const t of trades) (m.get(t.marketId) ?? m.set(t.marketId, []).get(t.marketId)!).push(t);
  return Array.from(m.entries()).map(([marketId, rows]) => {
    const wins = rows.filter((r) => r.netPnl > 0).length;
    return {
      marketId,
      symbol: marketSymbol(marketId),
      trades: rows.length,
      volume: rows.reduce((a, r) => a + r.volume, 0),
      netPnl: rows.reduce((a, r) => a + r.netPnl, 0),
      fees: rows.reduce((a, r) => a + r.fees, 0),
      winRatePct: rows.length ? (wins / rows.length) * 100 : null,
    };
  }).sort((a, b) => b.volume - a.volume);
}

// ---------------------------------------------------------------------------
// P/L by day, week and month
// ---------------------------------------------------------------------------

export type Grain = 'day' | 'week' | 'month';

export interface PeriodPnl {
  key: string;          // 2026-10-09 / 2026-W41 / 2026-10
  label: string;
  from: number;
  to: number;
  pnl: number;
  trades: number;
  volume: number;
  fees: number;
  funding: number;
}

function utcStartOfWeek(d: Date): Date {
  const x = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const dow = (x.getUTCDay() + 6) % 7;             // Monday = 0
  x.setUTCDate(x.getUTCDate() - dow);
  return x;
}

function bucketOf(ms: number, grain: Grain): { key: string; label: string; from: number; to: number } {
  const d = new Date(ms);
  if (grain === 'day') {
    const from = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
    const key = new Date(from).toISOString().slice(0, 10);
    return { key, label: key, from, to: from + 86_400_000 };
  }
  if (grain === 'week') {
    const s = utcStartOfWeek(d);
    const from = s.getTime();
    // ISO-ish week label from the Thursday of that week
    const thu = new Date(from + 3 * 86_400_000);
    const jan1 = Date.UTC(thu.getUTCFullYear(), 0, 1);
    const week = Math.floor((thu.getTime() - jan1) / (7 * 86_400_000)) + 1;
    return {
      key: `${thu.getUTCFullYear()}-W${String(week).padStart(2, '0')}`,
      label: `w/c ${new Date(from).toISOString().slice(0, 10)}`,
      from,
      to: from + 7 * 86_400_000,
    };
  }
  const from = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  const to = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  const key = new Date(from).toISOString().slice(0, 7);
  return {
    key,
    label: new Date(from).toLocaleDateString(undefined, { month: 'short', year: 'numeric', timeZone: 'UTC' }),
    from,
    to,
  };
}

/** Realized P/L grouped by UTC day, ISO week or calendar month. Newest first. */
export function pnlByPeriod(trades: ClosedTrade[], grain: Grain): PeriodPnl[] {
  const m = new Map<string, PeriodPnl>();
  for (const t of trades) {
    if (!t.at) continue;
    const b = bucketOf(t.at, grain);
    const cur = m.get(b.key) ?? { ...b, pnl: 0, trades: 0, volume: 0, fees: 0, funding: 0 };
    cur.pnl += t.netPnl;
    cur.trades += 1;
    cur.volume += t.volume;
    cur.fees += t.fees;
    cur.funding += t.funding;
    m.set(b.key, cur);
  }
  return Array.from(m.values()).sort((a, b) => b.from - a.from);
}

/** Realized P/L over the last `days` days (UTC), from the venue's own records. */
export function realizedSince(trades: ClosedTrade[], fromMs: number): number {
  return trades.filter((t) => t.at >= fromMs).reduce((a, t) => a + t.netPnl, 0);
}

export function startOfUtcToday(): number {
  const d = new Date();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

export function formatHold(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return '-';
  const s = ms / 1000;
  if (s < 90) return `${Math.round(s)}s`;
  const mn = s / 60;
  if (mn < 90) return `${Math.round(mn)}m`;
  const h = mn / 60;
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

// ---------------------------------------------------------------------------
// Volume and fees straight from the fills
// ---------------------------------------------------------------------------

export interface FillTotals {
  fills: number;
  volume: number;
  fees: number;
  builderFees: number;
  makerFills: number;
  takerFills: number;
  skippedUnknownMarket: number;
  covers: { from: number | null; to: number | null };
}

/**
 * Totals straight from the fills.
 *
 * `skippedUnknownMarket` matters and is shown in the UI: MARKET_CONFIGS only
 * holds LIVE markets, so a delisted one has no decimals and cannot be scaled.
 * On the owner's account that is 40 fills on market 30, and it accounts for
 * the whole difference between these fills' volume ($3,557,560.36) and the
 * venue's lifetime figure ($3,585,346.17), $27,785.81. Guessing decimals for
 * an unknown market would be worse than saying it is excluded.
 */
export function fillTotals(rows: any[]): FillTotals {
  let volume = 0; let fees = 0; let builder = 0; let maker = 0; let taker = 0; let skipped = 0;
  let from: number | null = null; let to: number | null = null;
  for (const f of rows ?? []) {
    // A fee is already in micro-USD and needs no market settings, so it is
    // counted for EVERY fill. Only the notional needs decimals. (Skipping the
    // fee as well left the all-time fee total $6.95 short of Perpl's own
    // figure: the fees on 40 fills in a delisted market.)
    fees += usd(f.f);
    builder += usd(f.bfa);
    if (n(f.l) === 1) maker += 1; else taker += 1;
    const sc = scales(n(f.mkt));
    if (!sc) { skipped += 1; continue; }
    volume += (n(f.p) / sc.pd) * (n(f.s) / sc.sd);
    const t = atMs(f);
    if (t) {
      from = from == null ? t : Math.min(from, t);
      to = to == null ? t : Math.max(to, t);
    }
  }
  return {
    fills: (rows ?? []).length,
    volume, fees, builderFees: builder,
    makerFills: maker, takerFills: taker,
    skippedUnknownMarket: skipped,
    covers: { from, to },
  };
}
