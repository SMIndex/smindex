// Featured traders, Tab 1 filters (docs/featured-traders spec, Filters). Kept
// in the URL like Discover and remembered per browser. Pure functions, no
// imports, so tests/featuredView.test.mts runs them with plain node.

export type FtSort = 'score' | 'win_rate_90d' | 'net_pnl_90d' | 'profit_factor_90d' | 'avg_win_usd' | 'max_drawdown_90d'
  | 'trades_per_week' | 'open_count' | 'last_trade_ms' | 'trades_90d' | 'account_value' | 'median_leverage';

export interface FtView {
  venue: 'all' | 'hl' | 'perpl';
  label: 'both' | 'consistent' | 'asymmetric';
  minWinRate: number;      // 0, 60, 70, 80, 90
  minPf: number;           // 0, 1.5, 2, 3
  minTrades: number;       // 30, 50, 100
  minProfit: number;       // 0 (= 500 default rule), 5000, 50000
  maxDd: number;           // 0 (any), 10, 20, 30
  size: '' | 'lt10k' | '10k-100k' | '100k-1m' | 'gt1m';
  hold: '' | 'scalper' | 'intraday' | 'swing';
  coins: string[];
  perplOnly: boolean;
  active: '24h' | '7d' | '14d' | 'any';
  maxLev: number;          // 0 (any), 5, 10, 20
  hideSmall: boolean;
  sort: FtSort;
  dir: 'desc' | 'asc';
}

export const FT_DEFAULT: FtView = {
  venue: 'all', label: 'both', minWinRate: 0, minPf: 0, minTrades: 30, minProfit: 0, maxDd: 0, size: '', hold: '',
  coins: [], perplOnly: false, active: '14d', maxLev: 0, hideSmall: true, sort: 'score', dir: 'desc',
};

export const WR_OPTS = [0, 60, 70, 80, 90];
export const PF_OPTS = [0, 1.5, 2, 3];
export const TRADES_OPTS = [30, 50, 100];
export const PROFIT_OPTS = [0, 5000, 50000];
export const DD_OPTS = [0, 10, 20, 30];
export const SIZE_OPTS: FtView['size'][] = ['', 'lt10k', '10k-100k', '100k-1m', 'gt1m'];
export const HOLD_OPTS: FtView['hold'][] = ['', 'scalper', 'intraday', 'swing'];
export const ACTIVE_OPTS: FtView['active'][] = ['24h', '7d', '14d', 'any'];
export const LEV_OPTS = [0, 5, 10, 20];
export const SORT_COLS: FtSort[] = ['score', 'win_rate_90d', 'net_pnl_90d', 'profit_factor_90d', 'avg_win_usd', 'max_drawdown_90d',
  'trades_per_week', 'open_count', 'last_trade_ms', 'trades_90d', 'account_value', 'median_leverage'];

const KEYS = ['venue', 'label', 'minWinRate', 'minPf', 'minTrades', 'minProfit', 'maxDd', 'size', 'hold', 'coins', 'perplOnly', 'active', 'maxLev', 'hideSmall', 'sort', 'dir'];

function pick<T>(raw: string | null, allowed: T[], parse: (s: string) => T, dflt: T): T {
  if (raw == null) return dflt;
  const v = parse(raw);
  return allowed.includes(v) ? v : dflt;
}

export function hasFtParams(p: URLSearchParams): boolean {
  return KEYS.some((k) => p.has(k));
}

export function parseFt(p: URLSearchParams): FtView {
  return {
    venue: pick(p.get('venue'), ['all', 'hl', 'perpl'] as const, (s) => s as FtView['venue'], 'all'),
    label: pick(p.get('label'), ['both', 'consistent', 'asymmetric'] as const, (s) => s as FtView['label'], 'both'),
    minWinRate: pick(p.get('minWinRate'), WR_OPTS, Number, 0),
    minPf: pick(p.get('minPf'), PF_OPTS, Number, 0),
    minTrades: pick(p.get('minTrades'), TRADES_OPTS, Number, 30),
    minProfit: pick(p.get('minProfit'), PROFIT_OPTS, Number, 0),
    maxDd: pick(p.get('maxDd'), DD_OPTS, Number, 0),
    size: pick(p.get('size'), SIZE_OPTS, (s) => s as FtView['size'], ''),
    hold: pick(p.get('hold'), HOLD_OPTS, (s) => s as FtView['hold'], ''),
    coins: (p.get('coins') || '').split(',').map((c) => c.trim().toUpperCase()).filter((c) => /^[A-Z0-9:._-]{1,20}$/.test(c)),
    perplOnly: p.get('perplOnly') === '1',
    active: pick(p.get('active'), ACTIVE_OPTS, (s) => s as FtView['active'], '14d'),
    maxLev: pick(p.get('maxLev'), LEV_OPTS, Number, 0),
    hideSmall: p.get('hideSmall') !== '0',
    sort: pick(p.get('sort'), SORT_COLS, (s) => s as FtSort, 'score'),
    dir: p.get('dir') === 'asc' ? 'asc' : 'desc',
  };
}

// Writes only the non-default values; other params are kept.
export function writeFt(p: URLSearchParams, v: FtView): URLSearchParams {
  const out = new URLSearchParams(p);
  KEYS.forEach((k) => out.delete(k));
  if (v.venue !== 'all') out.set('venue', v.venue);
  if (v.label !== 'both') out.set('label', v.label);
  if (v.minWinRate) out.set('minWinRate', String(v.minWinRate));
  if (v.minPf) out.set('minPf', String(v.minPf));
  if (v.minTrades !== 30) out.set('minTrades', String(v.minTrades));
  if (v.minProfit) out.set('minProfit', String(v.minProfit));
  if (v.maxDd) out.set('maxDd', String(v.maxDd));
  if (v.size) out.set('size', v.size);
  if (v.hold) out.set('hold', v.hold);
  if (v.coins.length) out.set('coins', v.coins.join(','));
  if (v.perplOnly) out.set('perplOnly', '1');
  if (v.active !== '14d') out.set('active', v.active);
  if (v.maxLev) out.set('maxLev', String(v.maxLev));
  if (!v.hideSmall) out.set('hideSmall', '0');
  if (v.sort !== 'score') out.set('sort', v.sort);
  if (v.dir !== 'desc') out.set('dir', v.dir);
  return out;
}

// The query the API takes (routers/featured.py list_wallets).
export function ftQuery(v: FtView): Record<string, string | number> {
  return {
    venue: v.venue,
    label: v.label === 'both' ? 'consistent,asymmetric' : v.label,
    minWinRate: v.minWinRate, minPf: v.minPf, minTrades: v.minTrades, minProfit: v.minProfit, maxDd: v.maxDd,
    size: v.size, hold: v.hold, coins: v.coins.join(','), perplOnly: v.perplOnly ? 1 : 0,
    active: v.active === 'any' ? 'any' : v.active, maxLev: v.maxLev, hideSmall: v.hideSmall ? 1 : 0,
    sort: v.sort, dir: v.dir,
  };
}

export function ftFiltersActive(v: FtView): number {
  let n = 0;
  if (v.venue !== 'all') n++;
  if (v.label !== 'both') n++;
  if (v.minWinRate) n++;
  if (v.minPf) n++;
  if (v.minTrades !== 30) n++;
  if (v.minProfit) n++;
  if (v.maxDd) n++;
  if (v.size) n++;
  if (v.hold) n++;
  if (v.coins.length) n++;
  if (v.perplOnly) n++;
  if (v.active !== '14d') n++;
  if (v.maxLev) n++;
  if (!v.hideSmall) n++;
  return n;
}

// Click on a header: a new column starts high to low, the same column flips.
export function clickFtSort(v: FtView, col: FtSort): FtView {
  if (v.sort === col) return { ...v, dir: v.dir === 'desc' ? 'asc' : 'desc' };
  return { ...v, sort: col, dir: col === 'max_drawdown_90d' ? 'asc' : 'desc' };
}

// ---- formatting shared by the screens (plain words, no jargon) ----

export function holdWords(sec: number | null | undefined): string {
  if (sec == null) return 'n/a';
  if (sec < 60) return `${Math.max(1, Math.round(sec))} s`;
  if (sec < 3600) return `${Math.round(sec / 60)} m`;
  if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.round((sec % 3600) / 60);
    return m ? `${h} h ${m} m` : `${h} h`;
  }
  const d = sec / 86400;
  if (d < 10) {
    const dd = Math.floor(d);
    const h = Math.round((sec - dd * 86400) / 3600);
    return h ? `${dd} d ${h} h` : `${dd} d`;
  }
  return `${Math.round(d)} d`;
}

export function holdShort(sec: number | null | undefined): string {
  if (sec == null) return 'n/a';
  if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))} m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(sec < 36000 ? 1 : 0).replace(/\.0$/, '')} h`;
  return `${(sec / 86400).toFixed(1).replace(/\.0$/, '')} d`;
}

export function agoWords(iso: string | null | undefined, nowMs = Date.now()): string {
  if (!iso) return 'no trade seen';
  const ms = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (!Number.isFinite(ms)) return 'n/a';
  const s = Math.max(0, (nowMs - ms) / 1000);
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} m ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function usd(n: number | null | undefined, signed = false, compact = false): string {
  if (n == null || !Number.isFinite(n)) return 'n/a';
  const sign = n < 0 ? '-' : signed ? '+' : '';
  const a = Math.abs(n);
  if (compact) {
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`;
    return `${sign}$${a.toFixed(a < 10 ? 2 : 0)}`;
  }
  if (a >= 1000) return `${sign}$${Math.round(a).toLocaleString('en-US')}`;
  return `${sign}$${a.toFixed(a < 10 ? 2 : 0)}`;
}

export function pct(n: number | null | undefined, digits = 0, signed = false): string {
  if (n == null || !Number.isFinite(n)) return 'n/a';
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

export function price(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return 'n/a';
  if (n >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 1 });
  if (n >= 1) return n.toLocaleString('en-US', { maximumFractionDigits: 3 });
  return n.toLocaleString('en-US', { maximumFractionDigits: 6 });
}

export function shortAddr(a: string): string {
  return a && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

export function labelWords(label: string | null | undefined): string {
  return label === 'consistent' ? 'Steady' : label === 'asymmetric' ? 'Big winners' : '';
}

export function coinsOnPerpl(top: string[], onPerpl: number): string {
  if (!top.length) return '';
  if (onPerpl >= top.length) return 'all on Perpl';
  if (onPerpl === 0) return 'none on Perpl';
  return `${onPerpl} of ${top.length} on Perpl`;
}
