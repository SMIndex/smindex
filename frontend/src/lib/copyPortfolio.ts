// Copy Trading v1 — Portfolio & History derivation (PURE, no I/O).
//
// Turns the real read-only copy endpoints into normalized rows + aggregates for
// /copy/portfolio and /copy/history. NEVER fabricates data:
//   - realized PnL comes straight from the stored copy position rows
//   - paper unrealized PnL comes from the API (read-time mark); if the API had no
//     mark, we treat it as UNAVAILABLE (null), never 0
//   - live unrealized PnL is computed from a caller-supplied live mark; no mark => null
//   - fees are NOT stored anywhere in the copy tables, so every fee here is an
//     ESTIMATE from lib/perplFees (open-side taker only; close fee = 0) and is
//     surfaced to the UI via `fee_is_estimated: true`
import type { PaperPosition, LivePosition, CopyOrder } from '@/lib/copyApi';
import { calculateEstimatedFee, normalizePerplFeeBps } from '@/lib/perplFees';

export type CopySource = 'paper' | 'live';

export interface NormalizedCopyPosition {
  key: string;
  source: CopySource;
  id: number;
  subscription_id: number | null;
  trader_wallet: string;
  follower_wallet: string;
  market_id: number;
  symbol: string;
  side: 'long' | 'short';
  entry_price: number | null;
  size: number | null;
  leverage: number | null;
  allocation_usd: number | null;
  status: string;
  is_open: boolean;
  mark_price: number | null;
  unrealized_pnl: number | null; // null = unavailable (no mark), NOT zero
  realized_pnl: number | null;
  close_price: number | null;
  close_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  notional: number | null;
  est_open_fee: number | null; // null = cannot estimate (no config/notional)
  fee_is_estimated: true;
  gross_pnl: number | null;
  est_net_pnl: number | null;
  roi: number | null;
}

export interface HistoryRow {
  key: string;
  kind: 'order' | 'position';
  source: CopySource;
  created_at: string | null;
  trader_wallet: string;
  follower_wallet: string;
  market_id: number;
  symbol: string;
  side: string;
  // NOTE: copy_orders does not store an order type, so there is no order_type field here.
  mode: string;
  status: string;
  entry_price: number | null;
  exit_price: number | null;
  size: number | null;
  leverage: number | null;
  allocation_usd: number | null;
  realized_pnl: number | null;
  est_fee: number | null;
  est_net_pnl: number | null;
  roi: number | null;
  perpl_order_id: string | null;
  perpl_request_id: string | null;
  perpl_fill_id: string | null;
  copy_order_id: number | null;
  close_reason: string | null;
  skip_reason: string | null;
  error_message: string | null;
}

export interface Resolvers {
  // Live mark for a market id (from the live market store). null when not cached.
  resolveMark?: (marketId: number) => number | null;
  // Taker fee bps for a market id (from MARKET_CONFIGS). null when unknown.
  resolveTakerBps: (marketId: number) => number | null;
}

// --------------------------- primitives ---------------------------

export function num(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Directional PnL. Returns null if any input is missing. */
export function positionPnl(
  side: string,
  entry: number | null,
  price: number | null,
  size: number | null,
): number | null {
  if (entry == null || price == null || size == null) return null;
  const s = Math.abs(size);
  return side === 'long' ? (price - entry) * s : (entry - price) * s;
}

function notionalOf(size: number | null, entry: number | null, alloc: number | null, lev: number | null): number | null {
  if (size != null && entry != null) return Math.abs(size) * entry;
  if (alloc != null && lev != null) return alloc * lev;
  return null;
}

/** Estimated OPEN taker fee. Close fee = 0 (Perpl charges open side only). */
function estOpenFee(notional: number | null, takerBps: number | null): number | null {
  if (notional == null || takerBps == null) return null;
  return calculateEstimatedFee(notional, normalizePerplFeeBps(takerBps));
}

function roiOf(gross: number | null, allocation: number | null): number | null {
  if (gross == null || allocation == null || allocation <= 0) return null;
  const r = (gross / allocation) * 100;
  return Number.isFinite(r) ? r : null;
}

// --------------------------- normalizers ---------------------------

export function normalizePaperPosition(p: PaperPosition, r: Resolvers): NormalizedCopyPosition {
  const is_open = p.status === 'open';
  const entry_price = num(p.entry_price);
  const size = num(p.size);
  const leverage = num(p.leverage);
  const allocation_usd = num(p.allocation_usd);
  const mark_price = is_open ? num(p.mark_price) : null;
  // API returns unrealized 0 even without a mark — treat "no mark" as UNAVAILABLE.
  const unrealized_pnl = is_open ? (mark_price != null ? num(p.unrealized_pnl) : null) : null;
  const realized_pnl = num(p.realized_pnl);
  const notional = notionalOf(size, entry_price, allocation_usd, leverage);
  const est_open_fee = estOpenFee(notional, r.resolveTakerBps(p.market_id));
  const gross_pnl = is_open
    ? (unrealized_pnl != null ? unrealized_pnl + (realized_pnl ?? 0) : null)
    : realized_pnl;
  // Net is unavailable (null) if the fee can't be estimated — never silently drop the fee.
  const est_net_pnl = (gross_pnl != null && est_open_fee != null) ? gross_pnl - est_open_fee : null;
  return {
    key: `paper:${p.id}`, source: 'paper', id: p.id, subscription_id: p.subscription_id ?? null,
    trader_wallet: p.trader_wallet, follower_wallet: p.follower_wallet,
    market_id: p.market_id, symbol: p.symbol, side: (p.side as 'long' | 'short'),
    entry_price, size, leverage, allocation_usd, status: p.status, is_open,
    mark_price, unrealized_pnl, realized_pnl,
    close_price: num(p.close_price), close_reason: p.close_reason,
    opened_at: p.opened_at, closed_at: p.closed_at,
    notional, est_open_fee, fee_is_estimated: true,
    gross_pnl, est_net_pnl, roi: roiOf(gross_pnl, allocation_usd),
  };
}

export function normalizeLivePosition(p: LivePosition, r: Resolvers): NormalizedCopyPosition {
  const is_open = p.status === 'open' || p.status === 'partially_closed';
  const entry_price = num(p.entry_price);
  const size = is_open ? (num(p.current_size) ?? num(p.entry_size)) : num(p.entry_size);
  const leverage = num(p.entry_leverage);
  const allocation_usd = num(p.entry_margin);
  const mark_price = is_open ? (r.resolveMark ? r.resolveMark(p.follower_market_id) : null) : null;
  const unrealized_pnl = is_open ? positionPnl(p.side, entry_price, mark_price, size) : null;
  const realized_pnl = num(p.realized_pnl);
  const notional = notionalOf(size, entry_price, allocation_usd, leverage);
  const est_open_fee = estOpenFee(notional, r.resolveTakerBps(p.follower_market_id));
  const gross_pnl = is_open
    ? (unrealized_pnl != null ? unrealized_pnl + (realized_pnl ?? 0) : null)
    : realized_pnl;
  // Net is unavailable (null) if the fee can't be estimated — never silently drop the fee.
  const est_net_pnl = (gross_pnl != null && est_open_fee != null) ? gross_pnl - est_open_fee : null;
  return {
    key: `live:${p.id}`, source: 'live', id: p.id, subscription_id: p.subscription_id ?? null,
    trader_wallet: p.trader_wallet, follower_wallet: p.follower_wallet,
    market_id: p.follower_market_id, symbol: p.symbol, side: p.side,
    entry_price, size, leverage, allocation_usd, status: p.status, is_open,
    mark_price, unrealized_pnl, realized_pnl,
    close_price: num(p.close_price), close_reason: p.close_reason,
    opened_at: p.opened_at, closed_at: p.closed_at,
    notional, est_open_fee, fee_is_estimated: true,
    gross_pnl, est_net_pnl, roi: roiOf(gross_pnl, allocation_usd),
  };
}

// --------------------------- aggregates ---------------------------

export interface PortfolioSummary {
  realized: number;
  unrealized: number;
  estFees: number;
  netEst: number;
  openCount: number;
  allocationOpen: number;
  winRate: number | null;
  closedCount: number;
  unrealizedIncomplete: boolean; // some open position had no mark
  feesIncomplete: boolean;       // some position's fee couldn't be estimated
}

function sum(xs: (number | null)[]): number {
  return xs.reduce<number>((a, v) => a + (v ?? 0), 0);
}

export function buildSummary(positions: NormalizedCopyPosition[]): PortfolioSummary {
  const open = positions.filter((p) => p.is_open);
  const closed = positions.filter((p) => !p.is_open && p.realized_pnl != null);
  const realized = sum(positions.map((p) => p.realized_pnl));
  const unrealized = sum(open.map((p) => p.unrealized_pnl));
  const estFees = sum(positions.map((p) => p.est_open_fee));
  const wins = closed.filter((p) => (p.realized_pnl ?? 0) > 0).length;
  return {
    realized,
    unrealized,
    estFees,
    netEst: realized + unrealized - estFees,
    openCount: open.length,
    allocationOpen: sum(open.map((p) => p.allocation_usd)),
    winRate: closed.length ? (wins / closed.length) * 100 : null,
    closedCount: closed.length,
    unrealizedIncomplete: open.some((p) => p.unrealized_pnl == null),
    feesIncomplete: positions.some((p) => p.est_open_fee == null),
  };
}

export interface TraderAgg {
  trader_wallet: string;
  open: number;
  closed: number;
  realized: number;
  unrealized: number;
  estNet: number;
  winRate: number | null;
  allocation: number;
  best: number | null;
  worst: number | null;
}

export function buildTraderBreakdown(positions: NormalizedCopyPosition[]): TraderAgg[] {
  const map = new Map<string, NormalizedCopyPosition[]>();
  for (const p of positions) {
    const k = p.trader_wallet.toLowerCase();
    (map.get(k) ?? map.set(k, []).get(k)!).push(p);
  }
  const out: TraderAgg[] = [];
  for (const [wallet, ps] of map) {
    const closed = ps.filter((p) => !p.is_open && p.realized_pnl != null);
    const wins = closed.filter((p) => (p.realized_pnl ?? 0) > 0).length;
    const nets = closed.map((p) => p.est_net_pnl).filter((v): v is number => v != null);
    out.push({
      trader_wallet: wallet,
      open: ps.filter((p) => p.is_open).length,
      closed: closed.length,
      realized: sum(ps.map((p) => p.realized_pnl)),
      unrealized: sum(ps.filter((p) => p.is_open).map((p) => p.unrealized_pnl)),
      estNet: sum(ps.map((p) => p.est_net_pnl)),
      winRate: closed.length ? (wins / closed.length) * 100 : null,
      allocation: sum(ps.map((p) => p.allocation_usd)),
      best: nets.length ? Math.max(...nets) : null,
      worst: nets.length ? Math.min(...nets) : null,
    });
  }
  return out.sort((a, b) => b.realized - a.realized);
}

export interface MarketAgg {
  market_id: number;
  symbol: string;
  trades: number;
  realized: number;
  unrealized: number;
  winRate: number | null;
  allocation: number;
}

export function buildMarketBreakdown(positions: NormalizedCopyPosition[]): MarketAgg[] {
  const map = new Map<number, NormalizedCopyPosition[]>();
  for (const p of positions) (map.get(p.market_id) ?? map.set(p.market_id, []).get(p.market_id)!).push(p);
  const out: MarketAgg[] = [];
  for (const [market_id, ps] of map) {
    const closed = ps.filter((p) => !p.is_open && p.realized_pnl != null);
    const wins = closed.filter((p) => (p.realized_pnl ?? 0) > 0).length;
    out.push({
      market_id,
      symbol: ps[0].symbol || String(market_id),
      trades: ps.length,
      realized: sum(ps.map((p) => p.realized_pnl)),
      unrealized: sum(ps.filter((p) => p.is_open).map((p) => p.unrealized_pnl)),
      winRate: closed.length ? (wins / closed.length) * 100 : null,
      allocation: sum(ps.map((p) => p.allocation_usd)),
    });
  }
  return out.sort((a, b) => b.realized - a.realized);
}

export interface CumulativePoint { t: string; value: number }

/** Cumulative REALIZED PnL over closed positions ordered by close time. Real data only. */
export function buildCumulativeRealized(positions: NormalizedCopyPosition[]): CumulativePoint[] {
  const closed = positions
    .filter((p) => !p.is_open && p.realized_pnl != null && p.closed_at)
    .sort((a, b) => (a.closed_at! < b.closed_at! ? -1 : 1));
  let running = 0;
  return closed.map((p) => {
    running += p.realized_pnl ?? 0;
    return { t: p.closed_at!, value: running };
  });
}

// --------------------------- history ---------------------------

export function buildHistory(
  orders: CopyOrder[],
  paper: PaperPosition[],
  live: LivePosition[],
  r: Resolvers,
): HistoryRow[] {
  // Map an opening copy_order -> perpl ids, so a live position row can show them.
  const orderById = new Map<number, CopyOrder>();
  for (const o of orders) orderById.set(o.id, o);

  const rows: HistoryRow[] = [];

  // (1) Every order ATTEMPT (incl. skipped / risk_blocked / failed).
  for (const o of orders) {
    const entry = num(o.fill_price) ?? num(o.intended_price);
    const size = num(o.fill_size) ?? num(o.intended_size);
    const alloc = num(o.allocation_usd);
    const lev = num(o.leverage);
    const notional = notionalOf(size, entry, alloc, lev);
    rows.push({
      key: `order:${o.id}`, kind: 'order', source: o.mode === 'paper' ? 'paper' : 'live',
      created_at: o.created_at, trader_wallet: o.trader_wallet, follower_wallet: o.follower_wallet,
      market_id: o.market_id, symbol: o.symbol, side: o.side,
      mode: o.mode, status: o.status,
      entry_price: entry, exit_price: null, size, leverage: lev, allocation_usd: alloc,
      realized_pnl: null, est_fee: estOpenFee(notional, r.resolveTakerBps(o.market_id)), est_net_pnl: null, roi: null,
      perpl_order_id: o.perpl_order_id, perpl_request_id: o.perpl_request_id, perpl_fill_id: o.perpl_fill_id,
      copy_order_id: o.id, close_reason: null, skip_reason: o.skip_reason, error_message: o.error_message,
    });
  }

  // (2) Closed position OUTCOMES (realized PnL / exit / close reason) — paper + live.
  const outcome = (p: NormalizedCopyPosition, openingOrder?: CopyOrder): HistoryRow => ({
    key: `${p.source}pos:${p.id}`, kind: 'position', source: p.source,
    created_at: p.closed_at, trader_wallet: p.trader_wallet, follower_wallet: p.follower_wallet,
    market_id: p.market_id, symbol: p.symbol, side: p.side,
    mode: p.source === 'paper' ? 'paper' : 'live_manual', status: p.status,
    entry_price: p.entry_price, exit_price: p.close_price, size: p.size, leverage: p.leverage,
    allocation_usd: p.allocation_usd, realized_pnl: p.realized_pnl, est_fee: p.est_open_fee,
    est_net_pnl: p.est_net_pnl, roi: p.roi,
    perpl_order_id: openingOrder?.perpl_order_id ?? null,
    perpl_request_id: openingOrder?.perpl_request_id ?? null,
    perpl_fill_id: openingOrder?.perpl_fill_id ?? null,
    copy_order_id: openingOrder?.id ?? null, close_reason: p.close_reason,
    skip_reason: null, error_message: null,
  });

  for (const p of paper) {
    if (p.status === 'open') continue;
    rows.push(outcome(normalizePaperPosition(p, r)));
  }
  for (const p of live) {
    if (p.status === 'open' || p.status === 'partially_closed') continue;
    const opening = p.copy_order_id != null ? orderById.get(p.copy_order_id) : undefined;
    rows.push(outcome(normalizeLivePosition(p, r), opening));
  }

  // Newest first.
  return rows.sort((a, b) => {
    const ta = a.created_at ?? '';
    const tb = b.created_at ?? '';
    return ta < tb ? 1 : ta > tb ? -1 : 0;
  });
}

export interface HistoryFilters {
  trader?: string;
  market_id?: number | 'all';
  side?: 'all' | 'long' | 'short';
  status?: string | 'all';
  source?: 'all' | 'paper' | 'live';
  pnl?: 'all' | 'positive' | 'negative';
  from?: string; // ISO date (inclusive)
  to?: string;   // ISO date (inclusive)
  search?: string; // order/request/fill id substring
}

export function applyHistoryFilters(rows: HistoryRow[], f: HistoryFilters): HistoryRow[] {
  const q = (f.search || '').trim().toLowerCase();
  return rows.filter((r) => {
    if (f.trader && r.trader_wallet.toLowerCase() !== f.trader.toLowerCase()) return false;
    if (f.market_id != null && f.market_id !== 'all' && r.market_id !== f.market_id) return false;
    if (f.side && f.side !== 'all' && r.side !== f.side) return false;
    if (f.status && f.status !== 'all' && r.status !== f.status) return false;
    if (f.source && f.source !== 'all' && r.source !== f.source) return false;
    if (f.pnl && f.pnl !== 'all') {
      if (r.realized_pnl == null) return false;
      if (f.pnl === 'positive' && r.realized_pnl <= 0) return false;
      if (f.pnl === 'negative' && r.realized_pnl >= 0) return false;
    }
    if (f.from && (r.created_at ?? '') < f.from) return false;
    if (f.to && (r.created_at ?? '') > f.to + 'T23:59:59') return false;
    if (q) {
      const hay = [r.perpl_order_id, r.perpl_request_id, r.perpl_fill_id, String(r.copy_order_id ?? '')]
        .filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}
