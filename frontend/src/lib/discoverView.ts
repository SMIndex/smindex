// Discover list: column sort + filters (2026-10-21). Pure functions over the
// trader rows the page already loaded; kept in the URL so a view can be shared
// and in localStorage so the browser remembers the last choice.
// No imports, so the test can run it with plain node.

export type SortCol = 'profit' | 'return' | 'volume' | 'open' | 'active';
export type ActiveWithin = '' | '24h' | '7d' | '30d';

export interface DiscoverView {
  sort: SortCol;
  dir: 'desc' | 'asc';
  minReturn: number;
  minProfit: number;
  minVolume: number;
  active: ActiveWithin;
  open: boolean;
  small: boolean;
}

export const DEFAULT_VIEW: DiscoverView = {
  sort: 'profit', dir: 'desc', minReturn: 0, minProfit: 0, minVolume: 0, active: '', open: false, small: false,
};

export const RETURN_OPTS = [0, 10, 25, 50, 100];
export const PROFIT_OPTS = [0, 1_000, 10_000, 50_000];
export const VOLUME_OPTS = [0, 100_000, 1_000_000, 10_000_000];
export const ACTIVE_OPTS: ActiveWithin[] = ['', '24h', '7d', '30d'];
export const SORT_COLS: SortCol[] = ['profit', 'return', 'volume', 'open', 'active'];
// Under the Return sort, traders below this profit are hidden unless "include small accounts" is on.
export const SMALL_PROFIT_USD = 500;

const ACTIVE_MS: Record<string, number> = { '24h': 86_400_000, '7d': 7 * 86_400_000, '30d': 30 * 86_400_000 };
const KEYS = ['sort', 'dir', 'minReturn', 'minProfit', 'minVolume', 'active', 'open', 'small'];

export interface ViewRow {
  rank: number;
  pnl_total: number;
  roi: number;
  volume: number;
  active_positions_count?: number | null;
  active_positions_pending?: boolean;
  has_active_positions?: boolean;
  last_fill_at?: string | null;
  activity?: { last_active_at?: string | null } | null;
}

function pick<T>(raw: string | null, allowed: T[], parse: (s: string) => T, dflt: T): T {
  if (raw == null) return dflt;
  const v = parse(raw);
  return allowed.includes(v) ? v : dflt;
}

export function hasViewParams(p: URLSearchParams): boolean {
  return KEYS.some((k) => p.has(k));
}

export function parseView(p: URLSearchParams): DiscoverView {
  return {
    sort: pick(p.get('sort'), SORT_COLS, (s) => s as SortCol, DEFAULT_VIEW.sort),
    dir: p.get('dir') === 'asc' ? 'asc' : 'desc',
    minReturn: pick(p.get('minReturn'), RETURN_OPTS, Number, 0),
    minProfit: pick(p.get('minProfit'), PROFIT_OPTS, Number, 0),
    minVolume: pick(p.get('minVolume'), VOLUME_OPTS, Number, 0),
    active: pick(p.get('active'), ACTIVE_OPTS, (s) => s as ActiveWithin, ''),
    open: p.get('open') === '1',
    small: p.get('small') === '1',
  };
}

// Writes only the non-default values; other params (wallets, asset) are kept.
export function writeView(p: URLSearchParams, v: DiscoverView): URLSearchParams {
  const out = new URLSearchParams(p);
  KEYS.forEach((k) => out.delete(k));
  if (v.sort !== DEFAULT_VIEW.sort) out.set('sort', v.sort);
  if (v.dir !== DEFAULT_VIEW.dir) out.set('dir', v.dir);
  if (v.minReturn) out.set('minReturn', String(v.minReturn));
  if (v.minProfit) out.set('minProfit', String(v.minProfit));
  if (v.minVolume) out.set('minVolume', String(v.minVolume));
  if (v.active) out.set('active', v.active);
  if (v.open) out.set('open', '1');
  if (v.small) out.set('small', '1');
  return out;
}

export function filtersActive(v: DiscoverView): boolean {
  return !!(v.minReturn || v.minProfit || v.minVolume || v.active || v.open);
}

// Same timestamp the row shows as "active ... ago".
export function lastActiveMs(t: ViewRow): number | null {
  const iso = t.activity?.last_active_at || t.last_fill_at;
  if (!iso) return null;
  const ms = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
  return Number.isFinite(ms) ? ms : null;
}

function sortValue(t: ViewRow, col: SortCol): number | null {
  switch (col) {
    case 'profit': return t.pnl_total ?? null;
    case 'return': return t.roi ?? null;
    case 'volume': return t.volume ?? null;
    case 'open': return t.active_positions_pending ? null : (t.active_positions_count ?? null);
    case 'active': return lastActiveMs(t);
  }
}

export function applyView<T extends ViewRow>(rows: T[], v: DiscoverView, now = Date.now()): { rows: T[]; smallHidden: number } {
  let smallHidden = 0;
  const out = rows.filter((t) => {
    if (v.minReturn && !(t.roi >= v.minReturn)) return false;
    if (v.minProfit && !(t.pnl_total >= v.minProfit)) return false;
    if (v.minVolume && !(t.volume >= v.minVolume)) return false;
    if (v.active) {
      const ms = lastActiveMs(t);
      if (ms == null || now - ms > ACTIVE_MS[v.active]) return false;
    }
    if (v.open && !(t.has_active_positions || t.active_positions_pending)) return false;
    if (v.sort === 'return' && !v.small && !(t.pnl_total >= SMALL_PROFIT_USD)) { smallHidden++; return false; }
    return true;
  });
  return { rows: sortRows(out, v), smallHidden };
}

// Missing values always go last, whichever way the column is sorted; ties keep the leaderboard rank.
export function sortRows<T extends ViewRow>(rows: T[], v: Pick<DiscoverView, 'sort' | 'dir'>): T[] {
  const sign = v.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = sortValue(a, v.sort);
    const y = sortValue(b, v.sort);
    if (x == null && y == null) return a.rank - b.rank;
    if (x == null) return 1;
    if (y == null) return -1;
    return x === y ? a.rank - b.rank : (x - y) * sign;
  });
}

// Click on a header: a new column starts high to low, the same column flips.
export function clickSort(v: DiscoverView, col: SortCol): DiscoverView {
  if (v.sort === col) return { ...v, dir: v.dir === 'desc' ? 'asc' : 'desc' };
  return { ...v, sort: col, dir: 'desc' };
}
