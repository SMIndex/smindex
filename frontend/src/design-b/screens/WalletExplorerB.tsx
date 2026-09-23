import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueries } from '@tanstack/react-query';
import {
  getExplorerHl, getExplorerFills, getExplorerLedger, getExplorerFunding,
  getExplorerOrders, getExplorerExtras, getExplorerPerpl, getExplorerPerplHistory,
  getExplorerActivity, isEvmAddress, normAddress,
  type ExplorerTier1, type ExplorerPosition, type EquitySeries, type ExplorerPerpl,
} from '@/lib/explorerApi';
import { getAnalyticsContext, type AnalyticsContext } from '@/lib/analyticsApi';
import { getTraderHlHistory, type HlHistoryResponse, type HlPositionHistory } from '@/lib/copyApi';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import TraderProfileModal, { HistoryDrawer } from '@/components/copy/TraderProfileModal';
import ExplorerSearch from '@/design-b/ExplorerSearch';

// Wallet Explorer — density pass. Layout is 1:1 with
// docs/design/wallet-explorer-layout.html (the visual contract): address
// header, five PnL stat cards, full-width equity, donut row, one dense tab
// panel per venue, Perpl stacked below Hyperliquid.
//
// All data comes from /api/explorer/* (+ our own analytics tables for cohort
// context) — the browser NEVER calls a venue. Every tab is PUBLIC: this data
// is public on both venues. Protection is unchanged (20 req/min/IP explorer
// bucket, server caches, explorer-priority shedding, lazy per-tab load).
// Absence renders as absence; nothing is invented to fill a panel.

type Pt = { t: number; v: number };

function relAge(unixOrIso: number | string | null | undefined): string {
  if (unixOrIso == null) return 'not available';
  const ms = typeof unixOrIso === 'number' ? unixOrIso * 1000
    : Date.parse(unixOrIso.endsWith('Z') ? unixOrIso : unixOrIso + 'Z');
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
function shortAge(unixOrIso: number | string | null | undefined): string {
  const a = relAge(unixOrIso);
  return a === 'just now' ? 'seconds' : a.replace(' ago', '');
}
/** Magnitude with the reference's minus glyph; never a bare number. */
function usd(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return '—';
  const neg = v < 0; const a = Math.abs(v);
  let body: string;
  if (a >= 1e9) body = `${(a / 1e9).toFixed(2)}B`;
  else if (a >= 1e6) body = `${(a / 1e6).toFixed(2)}M`;
  else if (a >= 1e4) body = `${(a / 1e3).toFixed(1)}K`;
  else body = a.toLocaleString(undefined, { maximumFractionDigits: digits });
  return `${neg ? '−' : ''}$${body}`;
}
function px(v: number | null | undefined): string {
  if (v == null || !v) return '—';
  return v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 })
    : v.toPrecision(5);
}
function amt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
}
function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v >= 0 ? '' : '−'}${Math.abs(v).toFixed(digits)}%`;
}
function shorten(w: string): string { return `${w.slice(0, 6)}…${w.slice(-4)}`; }
function sgn(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '';
  return v >= 0 ? 'long-c' : 'short-c';
}

/** Window PnL = last − first of the venue's cumulative pnlHistory. */
function windowPnl(s?: Pt[] | null): number | null {
  if (!s || s.length < 2) return null;
  return s[s.length - 1].v - s[0].v;
}
/** PnL over the trailing `secs` inside a longer series (48h from the week). */
function pnlSince(s: Pt[] | null | undefined, secs: number): number | null {
  if (!s || s.length < 2) return null;
  const last = s[s.length - 1];
  const cutoff = last.t - secs;
  let base: Pt | null = null;
  for (const p of s) { if (p.t <= cutoff) base = p; else break; }
  if (!base) return null;               // series does not reach back that far
  return last.v - base.v;
}

// ---- charts (no library; same honesty as the modal curve) ------------------

/** Full-width equity area chart: window pills, $0 gridline, hover crosshair,
 *  last-value label, coloured by the sign of the window's PnL. */
function EquityChart({ pts, height = 160 }: { pts: Pt[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!pts || pts.length < 2) {
    return <div className="empty">Not enough points in this window to draw a curve.</div>;
  }
  const W = 900; const H = height; const PAD = 8;
  const ts = pts.map((p) => p.t); const vs = pts.map((p) => p.v);
  const t0 = Math.min(...ts); const t1 = Math.max(...ts);
  let v0 = Math.min(...vs); let v1 = Math.max(...vs);
  if (v0 === v1) { v0 -= 1; v1 += 1; }
  const sx = (t: number) => PAD + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * PAD);
  const sy = (v: number) => H - PAD - ((v - v0) / (v1 - v0)) * (H - 2 * PAD);
  const first = pts[0].v; const last = pts[pts.length - 1].v;
  const up = last >= first;
  const col = up ? 'var(--long)' : 'var(--short)';
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.t).toFixed(1)},${sy(p.v).toFixed(1)}`).join(' ');
  const area = `${d} L${sx(t1).toFixed(1)},${H} L${sx(t0).toFixed(1)},${H} Z`;
  const zeroVisible = v0 < 0 && v1 > 0;
  const hp = hover != null ? pts[hover] : null;
  const hoverFrac = hp ? (sx(hp.t) / W) : 0;

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width <= 0) return;
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    setHover(Math.round(frac * (pts.length - 1)));
  };

  return (
    <div style={{ position: 'relative' }} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      <svg className="curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-label="equity curve">
        {zeroVisible && (
          <line x1="0" y1={sy(0)} x2={W} y2={sy(0)} stroke="var(--line)" strokeDasharray="4 4"
            strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
        <path d={area} fill={col} opacity="0.08" />
        <path d={d} fill="none" stroke={col} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {hp && <line x1={sx(hp.t)} y1="0" x2={sx(hp.t)} y2={H} stroke="var(--dim)"
          strokeWidth="1" vectorEffect="non-scaling-stroke" />}
      </svg>
      {zeroVisible && (
        <span style={{ position: 'absolute', left: 6, top: sy(0) - 14, fontSize: 10, color: 'var(--dim)' }}>$0</span>
      )}
      <span style={{
        position: 'absolute', right: 8, top: Math.max(0, Math.min(H - 16, sy(last) - 16)),
        fontSize: 11, fontWeight: 600, color: col,
      }}>{usd(last, 2)}</span>
      {hp && (
        <div style={{
          position: 'absolute', left: `${(hoverFrac * 100).toFixed(2)}%`, top: 0,
          transform: hoverFrac > 0.75 ? 'translateX(-104%)' : 'translateX(6px)',
          background: 'var(--s1)', border: '1px solid var(--line)', borderRadius: 8,
          padding: '4px 8px', fontSize: 11.5, pointerEvents: 'none', whiteSpace: 'nowrap',
        }}>
          <b className={sgn(hp.v)}>{usd(hp.v, 2)}</b>
          <span className="dim"> · {new Date(hp.t * 1000).toLocaleString()}</span>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--dim)', marginTop: 2 }}>
        <span>{new Date(t0 * 1000).toLocaleDateString()}</span>
        <span>{new Date(t1 * 1000).toLocaleDateString()}</span>
      </div>
    </div>
  );
}

/** Small line used for the Perpl snapshot-rank history. */
function MiniCurve({ pts, height = 90 }: { pts: Pt[]; height?: number }) {
  if (!pts || pts.length < 2) return <div className="empty">Not enough points.</div>;
  const w = 720; const h = height; const pad = 6;
  const ts = pts.map((p) => p.t); const vs = pts.map((p) => p.v);
  const t0 = Math.min(...ts); const t1 = Math.max(...ts);
  let v0 = Math.min(...vs); let v1 = Math.max(...vs);
  if (v0 === v1) { v0 -= 1; v1 += 1; }
  const sx = (t: number) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (w - 2 * pad);
  const sy = (v: number) => h - pad - ((v - v0) / (v1 - v0)) * (h - 2 * pad);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.t).toFixed(1)},${sy(p.v).toFixed(1)}`).join(' ');
  const up = pts[pts.length - 1].v >= pts[0].v;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', height: 'auto', display: 'block' }} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={up ? 'var(--long)' : 'var(--short)'} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const DONUT_C = 94.2;   // 2πr for r=15, matching the reference's dasharrays
function Donut({ slices }: { slices: { pct: number; color: string }[] }) {
  let off = 0;
  return (
    <svg viewBox="0 0 42 42" aria-hidden="true">
      <circle cx="21" cy="21" r="15" fill="none" stroke="var(--s2)" strokeWidth="7" />
      {slices.map((s, i) => {
        const len = Math.max(0, Math.min(DONUT_C, (s.pct / 100) * DONUT_C));
        const node = (
          <circle key={i} cx="21" cy="21" r="15" fill="none" stroke={s.color} strokeWidth="7"
            strokeDasharray={`${len.toFixed(2)} ${(DONUT_C - len).toFixed(2)}`}
            strokeDashoffset={(-off).toFixed(2)} transform="rotate(-90 21 21)" />
        );
        off += len;
        return node;
      })}
    </svg>
  );
}
const SLICE_COLORS = ['var(--amber)', 'var(--accent)', 'var(--long)', 'var(--short)', 'var(--dim)'];

function Stamp({ at, tip }: { at: number | string | null | undefined; tip: string }) {
  return <span className="stamp" title={tip}><i />{relAge(at)}</span>;
}

/** Skeleton shaped like the block it replaces (Design B: never a dash). */
function Skel({ h = 74, mb = 14 }: { h?: number; mb?: number }) {
  return <div style={{
    height: h, marginBottom: mb, borderRadius: 12, border: '1px solid var(--line)',
    background: 'linear-gradient(90deg, var(--s1), var(--s2), var(--s1))',
  }} />;
}

// ---- windows ---------------------------------------------------------------

const EQ_WINDOWS = [
  { key: 'day', label: '24h' }, { key: 'week', label: '7d' },
  { key: 'month', label: '30d' }, { key: 'allTime', label: 'All' },
] as const;
const PERP_KEY: Record<string, string> = {
  day: 'perpDay', week: 'perpWeek', month: 'perpMonth', allTime: 'perpAllTime',
};

const TABS = ['Positions', 'Transactions', 'Open orders', 'Deposit & withdraw',
  'Spot holdings', 'Funding', 'Order history', 'Extras', 'Activity'] as const;
type Tab = typeof TABS[number];


// ===========================================================================
// Screen
// ===========================================================================

export default function WalletExplorerB() {
  const { address } = useParams();
  const addr = address && isEvmAddress(address) ? normAddress(address) : null;

  if (!address) {
    return (
      <div className="screen">
        <div className="head"><div><h1>Wallet Explorer</h1>
          <p>Any wallet, both venues — Hyperliquid live state, equity and history · Perpl on-chain account, positions and leaderboard. No wallet connection needed.</p></div></div>
        <div className="card" style={{ maxWidth: 560 }}>
          <h2>Look up an address</h2>
          <div className="sub">Validated 0x address; agent and sub-account wallets resolve to their master.</div>
          <div style={{ marginTop: 10 }}><ExplorerSearch /></div>
        </div>
      </div>
    );
  }
  if (!addr) {
    return (
      <div className="screen"><div className="card"><div className="empty">
        Not a valid 0x address: <span className="mono">{address}</span>
      </div><div style={{ marginTop: 10, maxWidth: 480 }}><ExplorerSearch /></div></div></div>
    );
  }
  return <ExplorerBody addr={addr} />;
}


function ExplorerBody({ addr }: { addr: string }) {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);
  const [profileOpen, setProfileOpen] = useState(false);

  const hl = useQuery({
    queryKey: ['explorer-hl', addr],
    queryFn: () => getExplorerHl(addr),
    staleTime: 300_000,
    retry: 1,
  });
  const perpl = useQuery({
    queryKey: ['explorer-perpl', addr],
    queryFn: () => getExplorerPerpl(addr),
    staleTime: 300_000,
    retry: 1,
  });
  const d = hl.data as ExplorerTier1 | undefined;
  const p = perpl.data as ExplorerPerpl | undefined;

  const onWatch = async () => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(addr, 'hl')) await watchlist.unwatch(addr, 'hl');
    else await watchlist.watch(addr, 'hl');
  };

  const res = d?.resolution;
  const cohort = d?.cohort;
  const fs = cohort?.fill_stats;
  // period keys from our ingest are all/day/week/month — prefer all-time
  const hlRankKey = ['all', 'month', 'week', 'day'].find((k) => cohort?.ranks?.[k]);
  const hlRank = hlRankKey ? cohort!.ranks[hlRankKey] : null;

  return (
    <div className="screen">
      {/* ---- 1. ADDRESS HEADER ---- */}
      <div className="xp-hdr">
        <span className="xp-addr mono" style={{ userSelect: 'all' }} title={addr}>
          {cohort?.display_name || shorten(addr)}
        </span>
        {hlRank && (
          <span className="xp-tag" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
            title={`Our HL leaderboard ingest, ${hlRankKey} window (top-100 per window)`}>
            HL rank #{hlRank.rank}{hlRankKey !== 'all' ? ` (${hlRankKey})` : ''}
          </span>
        )}
        {fs && (fs.profit_factor != null || fs.win_rate_7d != null) && (
          <span className="xp-tag soft" title={`7-day sampled fills${fs.sample_capped ? ' (capped sample)' : ''} · computed ${relAge(fs.computed_at)}`}>
            {fs.profit_factor != null && `PF ${fs.profit_factor >= 999 ? '∞' : fs.profit_factor.toFixed(2)}`}
            {fs.win_rate_7d != null && ` · win ${(fs.win_rate_7d * 100).toFixed(0)}% (${fs.trades_7d.toLocaleString()})`}
            {fs.max_drawdown_7d != null && ` · maxDD ${(Math.abs(fs.max_drawdown_7d) * 100).toFixed(0)}%`}
          </span>
        )}
        {p?.perpl_account && (
          <span className="xp-tag line">Perpl acct #{p.account_id}</span>
        )}
        {res && !['user', 'missing', 'unresolved'].includes(res.role) && (
          <span className="xp-tag" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>{res.role}</span>
        )}
        {cohort?.flags?.mm === true && (
          <span className="xp-tag" style={{ background: 'var(--amber-soft)', color: 'var(--amber)' }}
            title="Market-maker pattern (two-clause evidence)">MM</span>
        )}
        {cohort?.flags?.hedger === true && (
          <span className="xp-tag" style={{ background: 'var(--amber-soft)', color: 'var(--amber)' }}
            title="Spot book offsets perp shorts (inventory hedger)">hedger</span>
        )}
        {cohort?.in_cohort === true && <span className="xp-tag soft" title={cohort.source}>cohort</span>}
        <span className="xp-actions">
          <button className="btn sm" onClick={onWatch} disabled={!isAuthenticated}
            title={isAuthenticated ? '' : 'Connect wallet to watch'}>
            {watchlist.isWatched(addr, 'hl') ? '★ Watching' : 'Watch'}
          </button>
          <button className="btn sm" onClick={() => navigate('/alerts')}>Alerts</button>
          <button className="btn sm primary" onClick={() => setProfileOpen(true)}>Copy</button>
        </span>
      </div>
      <div className="mono sub xp-addr-full" style={{ marginTop: -8, marginBottom: 14, userSelect: 'all' }}>{addr}</div>

      {/* resolution banners */}
      {(res?.role === 'agent' || res?.role === 'subAccount') && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="kv" style={{ borderTop: 'none' }}>
            <span>{res.role === 'agent' ? 'Agent wallet' : 'Sub-account'}{res.master ? ' of' : ''}</span>
            {res.master
              ? <button className="btn sm ghost mono" onClick={() => navigate(`/wallet/${res.master}`)}>{shorten(res.master)} →</button>
              : <b>master unknown</b>}
          </div>
        </div>
      )}
      {res?.role === 'vault' && d?.vault && (
        <div className="card" style={{ marginBottom: 14 }}>
          <h2>Vault{d.vault.name ? ` — ${d.vault.name}` : ''}</h2>
          <div className="kv2" style={{ marginTop: 8 }}>
            <div><span>Leader</span><b className="mono">{d.vault.leader ? shorten(d.vault.leader) : '—'}</b></div>
            <div><span>APR</span><b>{d.vault.apr != null ? `${(d.vault.apr * 100).toFixed(1)}%` : '—'}</b></div>
            <div><span>Followers</span><b>{d.vault.follower_count ?? '—'}</b></div>
            <div><span>Status</span><b>{d.vault.is_closed ? 'closed' : 'open'}</b></div>
          </div>
        </div>
      )}

      {/* ---- HYPERLIQUID ---- */}
      {hl.isLoading && <><Skel h={74} /><Skel h={200} /><Skel h={96} /><Skel h={260} /></>}
      {hl.isError && (
        <div className="card" style={{ marginBottom: 14 }}><div className="empty">
          Hyperliquid lookup failed{(hl.error as { response?: { status?: number } })?.response?.status === 503
            ? ' — explorer budget busy, retry in ~30s' : ''}.{' '}
          <button className="btn sm" onClick={() => hl.refetch()}>Retry</button>
        </div></div>
      )}
      {d && d.hl_account === false && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="empty">No Hyperliquid account found for this address.</div>
        </div>
      )}
      {d?.hl_account && <HlSection addr={addr} d={d} />}

      {/* ---- PERPL (second venue block, below HL) ---- */}
      <PerplSection addr={addr} q={perpl} />

      {profileOpen && (
        <TraderProfileModal
          wallet={addr}
          exchange="hl"
          isWatched={watchlist.isWatched(addr, 'hl')}
          onWatchToggle={onWatch}
          onCreateSub={subs.create}
          onClose={() => setProfileOpen(false)}
        />
      )}
    </div>
  );
}


// ===========================================================================
// Hyperliquid section
// ===========================================================================

function HlSection({ addr, d }: { addr: string; d: ExplorerTier1 }) {
  const [eqWindow, setEqWindow] = useState<string>('month');
  const [perpOnly, setPerpOnly] = useState(false);
  const [eqMetric, setEqMetric] = useState<'pnl' | 'account_value'>('pnl');
  const [tab, setTab] = useState<Tab>('Positions');

  const eq = (d.equity || {}) as Record<string, EquitySeries>;
  const eqSeries: EquitySeries | null = useMemo(() => {
    const key = perpOnly ? PERP_KEY[eqWindow] : eqWindow;
    return eq[key] ?? null;
  }, [eq, eqWindow, perpOnly]);

  // The five headline windows, all from the ONE portfolio payload tier-1
  // already fetched. 48h has no venue window — computed from the week series.
  const stats = useMemo(() => ([
    { label: 'Total PnL', v: windowPnl(eq.allTime?.pnl), tip: 'allTime pnlHistory · venue-computed, includes unrealized' },
    { label: '24h PnL', v: windowPnl(eq.day?.pnl), tip: 'day pnlHistory · venue-computed, includes unrealized' },
    { label: '48h PnL', v: pnlSince(eq.week?.pnl, 172800), tip: 'computed here from week.pnlHistory (the venue has no 48h window) · includes unrealized' },
    { label: '7d PnL', v: windowPnl(eq.week?.pnl), tip: 'week pnlHistory · venue-computed, includes unrealized' },
    { label: '30d PnL', v: windowPnl(eq.month?.pnl), tip: 'month pnlHistory · venue-computed, includes unrealized' },
  ]), [eq]);

  // Net deposits comes from the ledger tab. It is NOT fetched on load — that
  // would add venue weight to every cold visit — so the caption appears once
  // the Deposit & withdraw tab has been opened (its payload is then cached).
  const ledgerCached = useQuery({
    queryKey: ['explorer-tab', addr, 'Deposit & withdraw'],
    queryFn: () => getExplorerLedger(addr),
    enabled: false,
  });
  const netDeposits = ledgerCached.data?.net_deposits ?? null;

  const positions = d.positions ?? [];
  const grossNotional = positions.reduce((a, p) => a + Math.abs(p.notional || 0), 0);
  const aggLeverage = d.account_value ? grossNotional / d.account_value : null;
  const assetShares = useMemo(() => {
    if (!grossNotional) return [];
    return positions
      .map((p) => ({ coin: p.coin, share: (Math.abs(p.notional || 0) / grossNotional) * 100 }))
      .sort((a, b) => b.share - a.share);
  }, [positions, grossNotional]);

  const spotUsdc = d.spot?.usdc ?? null;
  const unpricedSpot = (d.spot?.balances ?? []).filter((b) => b.coin !== 'USDC').length;
  const totalValue = (d.account_value ?? 0) + (spotUsdc ?? 0);
  const withdrawable = d.withdrawable ?? null;
  const freePct = d.account_value ? ((withdrawable ?? 0) / d.account_value) * 100 : null;

  const stamp = d.state_fetched_at ?? d.fetched_at;

  return (
    <>
      {/* ---- 2. FIVE PNL STAT CARDS ---- */}
      <div className="xp-stats">
        {stats.map((s) => (
          <div className="xp-stat" key={s.label} title={s.tip}>
            <div className="lbl">
              {s.label}
              {s.label === '30d PnL' && netDeposits != null && ` · net deposits ${usd(netDeposits)}`}
            </div>
            <div className={`val ${s.v == null ? 'dim' : sgn(s.v)}`}>
              {s.v == null ? 'Not available' : usd(s.v, 2)}
            </div>
          </div>
        ))}
      </div>

      {/* ---- 3. EQUITY ---- */}
      <div className="card xp-eq">
        <div className="top">
          <span style={{ fontSize: 13, fontWeight: 600 }}>
            Equity <span className="dim" style={{ fontWeight: 400 }}>· {d.equity_label || 'venue PnL, includes unrealized'}{perpOnly ? ' · perp only' : ''}</span>
          </span>
          <span className="xp-pills">
            {EQ_WINDOWS.map((w) => (
              <button key={w.key} className="xp-pill" aria-pressed={eqWindow === w.key}
                onClick={() => setEqWindow(w.key)}>{w.label}</button>
            ))}
            <button className="xp-pill" aria-pressed={eqMetric === 'pnl'} onClick={() => setEqMetric('pnl')}>PnL</button>
            <button className="xp-pill" aria-pressed={eqMetric === 'account_value'} onClick={() => setEqMetric('account_value')}>Value</button>
            <button className="xp-pill bordered" aria-pressed={perpOnly} onClick={() => setPerpOnly(!perpOnly)}>Perp only</button>
            <Stamp at={d.fetched_at} tip={d.provenance?.equity || 'venue portfolio history'} />
          </span>
        </div>
        {eqSeries
          ? <EquityChart pts={eqSeries[eqMetric] || []} />
          : <div className="empty">{perpOnly
            ? 'The venue returned no perp-only series for this window.'
            : 'The venue returned no equity history for this window.'}</div>}
      </div>

      {/* ---- 4. DONUT ROW ---- */}
      <div className="xp-donuts">
        <div className="xp-donut">
          <Donut slices={assetShares.slice(0, 5).map((a, i) => ({ pct: a.share, color: SLICE_COLORS[i % SLICE_COLORS.length] }))} />
          <div>
            <div className="lbl">Perps position value</div>
            <div className="big">{grossNotional ? usd(grossNotional) : '$0'}</div>
            <div className="cap">
              {positions.length} position{positions.length === 1 ? '' : 's'}
              {aggLeverage != null && ` · aggregate ${aggLeverage.toFixed(1)}x`}
              {assetShares.slice(0, 3).map((a, i) => (
                <span key={a.coin}>{' · '}<span style={{ color: SLICE_COLORS[i % SLICE_COLORS.length] }}>{a.coin} {a.share.toFixed(0)}%</span></span>
              ))}
            </div>
          </div>
        </div>

        <div className="xp-donut">
          <Donut slices={totalValue > 0 ? [
            { pct: ((d.account_value ?? 0) / totalValue) * 100, color: 'var(--long)' },
            { pct: ((spotUsdc ?? 0) / totalValue) * 100, color: 'var(--short)' },
          ] : []} />
          <div>
            <div className="lbl">Account total value</div>
            <div className="big">{usd(totalValue, 2)}</div>
            <div className="cap" title={d.spot?.note || ''}>
              Perpetual {usd(d.account_value, 2)} · Spot USDC {d.spot ? usd(spotUsdc, 2) : 'unavailable'}
              {unpricedSpot > 0 && (
                <> · <span className="dim" title="Spot coins are reported in raw units — we never price them with perp mids (unit-mismatch rule). Balances are in the Spot holdings tab.">
                  +{unpricedSpot} spot asset{unpricedSpot === 1 ? '' : 's'} not priced
                </span></>
              )}
            </div>
          </div>
        </div>

        <div className="xp-donut">
          <Donut slices={d.account_value ? [{
            pct: Math.max(0, Math.min(100, 100 - (freePct ?? 0))),
            color: (freePct ?? 0) <= 0 ? 'var(--short)' : 'var(--accent)',
          }] : []} />
          <div>
            <div className="lbl">Free margin</div>
            <div className="big">{usd(withdrawable, 2)}</div>
            <div className={`cap ${(freePct ?? 1) <= 0 ? 'short-c' : ''}`}>
              {freePct == null ? 'Not available'
                : freePct <= 0 ? 'Withdrawable 0.0% · margin fully used'
                  : `Withdrawable ${freePct.toFixed(1)}% · margin used ${usd(d.total_margin_used, 2)}`}
            </div>
          </div>
        </div>
      </div>

      {/* ---- 5. TABS ---- */}
      <div className="card xp-panel">
        <div className="xp-tabs" role="tablist">
          {TABS.map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
              {t === 'Activity' ? 'Activity · both venues' : t}
            </button>
          ))}
        </div>

        {tab === 'Positions' && <PositionsTable addr={addr} d={d} />}
        {tab === 'Open orders' && <OpenOrdersTable d={d} />}
        {tab === 'Spot holdings' && <SpotTable d={d} />}
        {tab !== 'Positions' && tab !== 'Open orders' && tab !== 'Spot holdings' && (
          <LazyTab addr={addr} tab={tab} />
        )}

        <div className="xp-foot">
          <span>Hyperliquid · as of {shortAge(stamp)} · {d.provenance?.state || 'full state'} · {d.hip3}</span>
          <span>tier-1 measured weight {d.weight_cost} in {d.requests} venue call{d.requests === 1 ? '' : 's'} · tabs load on demand</span>
        </div>
      </div>
    </>
  );
}


// ---- Positions (tier-1 data + cohort context + build history) --------------

function PositionsTable({ addr, d }: { addr: string; d: ExplorerTier1 }) {
  const [open, setOpen] = useState<string | null>(null);
  const positions = d.positions ?? [];

  // Build history: our own dating-worker cache, no venue call.
  const hist = useQuery<HlHistoryResponse>({
    queryKey: ['explorer-hl-history', addr],
    queryFn: () => getTraderHlHistory(addr),
    enabled: positions.length > 0,
    staleTime: 300_000,
    retry: 1,
  });
  const histFor = (coin: string, side: string): HlPositionHistory | undefined =>
    hist.data?.histories?.find((h) => h.coin === coin && h.side === side);

  // Cohort context per asset — our analytics tables, ZERO venue calls.
  const coins = useMemo(() => Array.from(new Set(positions.map((p) => p.coin))), [positions]);
  const ctxQs = useQueries({
    queries: coins.map((c) => ({
      queryKey: ['explorer-ctx', c],
      queryFn: () => getAnalyticsContext(c),
      staleTime: 300_000,
      retry: 0 as const,
    })),
  });
  const ctxFor = (coin: string): AnalyticsContext | undefined => {
    const i = coins.indexOf(coin);
    return i >= 0 ? (ctxQs[i]?.data as AnalyticsContext | undefined) : undefined;
  };

  if (positions.length === 0) return <div className="empty">No open perp positions.</div>;

  return (
    <div className="xp-scroll">
      <table className="xp-tbl">
        <thead><tr>
          <th>Token</th><th>Side</th><th>Leverage</th><th className="r">Value</th><th className="r">Amount</th>
          <th className="r">Entry</th><th className="r">Mark</th><th className="r">Liq price</th><th className="r">PnL</th>
          <th className="r">Funding</th><th className="r">Opened</th><th></th>
        </tr></thead>
        <tbody>
          {positions.map((p, i) => {
            const key = `${p.coin}-${p.side}`;
            const h = histFor(p.coin, p.side);
            const ctx = ctxFor(p.coin);
            const line = contextLine(p, ctx, h);
            const expanded = open === key;
            return [
              <tr key={`r${i}`}>
                <td style={{ fontWeight: 600 }}>{p.coin}
                  {p.perpl_market_id == null && <span className="dim" title="Not listed on Perpl"> ·</span>}</td>
                <td><span className={`xp-side ${p.side === 'long' ? 'long-c' : 'short-c'}`}>
                  {p.side === 'long' ? 'Long' : 'Short'}</span></td>
                <td>{p.leverage ? `${p.leverage}x` : '—'}{p.margin_mode ? ` ${p.margin_mode}` : ''}</td>
                <td className="r">{usd(p.notional, 2)}</td>
                <td className="r">{p.side === 'short' ? `−${amt(Math.abs(p.size))}` : amt(p.size)}</td>
                <td className="r mono">{px(p.entry_px)}</td>
                <td className="r mono">{px(p.mark_px)}</td>
                <td className="r mono short-c">{px(p.liquidation_px)}</td>
                <td className={`r ${sgn(p.unrealized_pnl)}`} style={{ fontWeight: 600 }}>
                  {usd(p.unrealized_pnl, 2)}{p.roe != null && ` · ${pct(p.roe * 100)}`}
                </td>
                <td className={`r ${sgn(p.funding_since_open != null ? -p.funding_since_open : null)}`}
                  title="cumFunding.sinceOpen (venue)">
                  {p.funding_since_open != null ? usd(-p.funding_since_open, 2) : '—'}
                </td>
                <td className="r" title={p.opened_at_source ? `source: ${p.opened_at_source}` : ''}>
                  {p.opened_at ? relAge(p.opened_at).replace(' ago', '')
                    : p.opened_before ? <>{relAge(p.opened_before).replace(' ago', '')} <span className="dim" title="earliest provably-open moment — the true open is at or before this">≈</span></>
                      : p.opened_at_pending ? <span className="dim">dating…</span> : '—'}
                </td>
                <td className="dim">
                  {h ? (
                    <button onClick={() => setOpen(expanded ? null : key)} className="dim"
                      title="Position build history — how this position was built, fill by fill"
                      style={{ transform: expanded ? 'rotate(90deg)' : 'none', display: 'inline-block' }}>▸</button>
                  ) : null}
                </td>
              </tr>,
              line ? <tr key={`c${i}`} className="ctxrow"><td colSpan={12} className="xp-ctx">{line}</td></tr> : null,
              expanded && h ? (
                <tr key={`h${i}`} className="ctxrow"><td colSpan={12} style={{ padding: '0 8px 8px' }}>
                  <div className="tbwrap"><HistoryDrawer h={h} ageSec={hist.data?.fills_age_sec ?? null} /></div>
                </td></tr>
              ) : null,
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

/** "↳ against the 15-wallet crowd long near $78k · smart money net long BTC
 *  (SMI 66) · built over 4 adds, first fill ~$79.4k" — only the clauses we
 *  actually have. Returns null when we track nothing for this asset. */
function contextLine(p: ExplorerPosition, ctx: AnalyticsContext | undefined,
                     h: HlPositionHistory | undefined): string | null {
  const parts: string[] = [];
  if (ctx?.available) {
    const opposite = p.side === 'long' ? 'short' : 'long';
    const crowd = ctx.crowding?.[opposite as 'long' | 'short'];
    if (crowd && crowd.wallet_count > 0) {
      const near = crowd.entry_lo && crowd.entry_hi
        ? usd((crowd.entry_lo + crowd.entry_hi) / 2) : null;
      parts.push(`against the ${crowd.wallet_count}-wallet crowd ${crowd.side}${near ? ` near ${near}` : ''}`);
    }
    if (ctx.stance) {
      const label = ctx.stance === 'balanced' ? 'balanced on ' : `net ${ctx.stance.replace('net_', '')} `;
      parts.push(`smart money ${label}${ctx.asset}${ctx.smi != null ? ` (SMI ${Math.round(ctx.smi)}${ctx.smi_calibrating ? ', calibrating' : ''})` : ''}`);
    }
  }
  if (h && h.adds_count >= 2) {
    parts.push(`built over ${h.adds_count} adds, first fill ~${px(h.first_fill_px)}`);
  }
  return parts.length ? `↳ ${parts.join(' · ')}` : null;
}


function OpenOrdersTable({ d }: { d: ExplorerTier1 }) {
  const rows = [
    ...(d.adds ?? []).map((o) => ({ o, kind: 'resting add' })),
    ...(d.pending_entries ?? []).map((o) => ({ o, kind: 'pending entry (trigger)' })),
  ];
  if (rows.length === 0) {
    return <div className="empty">No resting non-trigger orders. Position TP/SL triggers are listed per position.</div>;
  }
  return (
    <div className="xp-scroll">
      <table className="xp-tbl">
        <thead><tr><th>Token</th><th>Kind</th><th>Side</th><th className="r">Limit</th><th className="r">Trigger</th><th className="r">Size</th></tr></thead>
        <tbody>
          {rows.map(({ o, kind }, i) => (
            <tr key={i}>
              <td style={{ fontWeight: 600 }}>{o.coin}</td>
              <td className="dim">{kind}</td>
              <td><span className={`xp-side ${o.side === 'buy' ? 'long-c' : 'short-c'}`}>{o.side}</span></td>
              <td className="r mono">{px(o.limit_px)}</td>
              <td className="r mono">{px(o.trigger_px)}</td>
              <td className="r">{amt(o.size)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SpotTable({ d }: { d: ExplorerTier1 }) {
  if (!d.spot) return <div className="empty">Spot state unavailable from the venue on this fetch.</div>;
  const b = d.spot.balances ?? [];
  if (b.length === 0) return <div className="empty">No spot balances.</div>;
  return (
    <>
      <div className="xp-scroll">
        <table className="xp-tbl">
          <thead><tr><th>Coin</th><th className="r">Balance</th><th className="r">On hold</th><th className="r">Value</th><th className="r">Entry notional</th></tr></thead>
          <tbody>
            {b.map((x, i) => (
              <tr key={i}>
                <td style={{ fontWeight: 600 }}>{x.coin}</td>
                <td className="r">{amt(x.total)}</td>
                <td className="r dim">{x.hold ? amt(x.hold) : '—'}</td>
                <td className="r">{x.coin === 'USDC' ? usd(x.total, 2) : <span className="dim" title="Spot coins are not priced with perp mids (unit-mismatch rule).">not priced</span>}</td>
                <td className="r dim">{x.entry_ntl != null ? usd(x.entry_ntl, 2) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sub" style={{ marginTop: 8 }}>{d.spot.note}</div>
    </>
  );
}


// ---- lazy public tabs ------------------------------------------------------

function LazyTab({ addr, tab }: { addr: string; tab: Tab }) {
  const q = useQuery({
    queryKey: ['explorer-tab', addr, tab],
    queryFn: async (): Promise<unknown> => {
      if (tab === 'Transactions') return getExplorerFills(addr);
      if (tab === 'Deposit & withdraw') return getExplorerLedger(addr);
      if (tab === 'Funding') return getExplorerFunding(addr);
      if (tab === 'Order history') return getExplorerOrders(addr);
      if (tab === 'Extras') return getExplorerExtras(addr);
      return null;
    },
    enabled: tab !== 'Activity',
    staleTime: 600_000,
    retry: 1,
  });
  if (tab === 'Activity') return <ActivityTable addr={addr} />;
  if (q.isLoading) return <Skel h={180} mb={0} />;
  if (q.isError) {
    const st = (q.error as { response?: { status?: number } })?.response?.status;
    return <div className="empty">
      {st === 503 ? 'Explorer budget busy — retry in ~30s.' : `Could not load ${tab.toLowerCase()}.`}{' '}
      <button className="btn sm" onClick={() => q.refetch()}>Retry</button>
    </div>;
  }
  const meta = q.data as { fetched_at: number; weight_cost: number };
  return (
    <>
      <div className="sub" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
        <span>{coverageLine(tab, q.data)}</span>
        <Stamp at={meta?.fetched_at} tip={`venue call at explorer priority · measured weight ${meta?.weight_cost}`} />
      </div>
      <div className="xp-scroll" style={{ maxHeight: 460, overflowY: 'auto' }}>
        {tab === 'Transactions' && <FillsTable d={q.data as Awaited<ReturnType<typeof getExplorerFills>>} />}
        {tab === 'Deposit & withdraw' && <LedgerTable d={q.data as Awaited<ReturnType<typeof getExplorerLedger>>} />}
        {tab === 'Funding' && <FundingTable d={q.data as Awaited<ReturnType<typeof getExplorerFunding>>} />}
        {tab === 'Order history' && <OrdersTable d={q.data as Awaited<ReturnType<typeof getExplorerOrders>>} />}
        {tab === 'Extras' && <ExtrasPanel d={q.data as Awaited<ReturnType<typeof getExplorerExtras>>} />}
      </div>
    </>
  );
}

function coverageLine(tab: Tab, data: unknown): string {
  const d = data as Record<string, number | string | boolean>;
  if (!d) return '';
  if (tab === 'Transactions') return `last ${Number(d.count).toLocaleString()} shown · ${d.retention_note}${d.truncated ? ' · TRUNCATED at the page cap' : ''}`;
  if (tab === 'Deposit & withdraw') return `${Number(d.count).toLocaleString()} entries · net deposits ${usd(Number(d.net_deposits))} · in ${usd(Number(d.deposits))} / out ${usd(Number(d.withdrawals))} · ${d.note}`;
  if (tab === 'Funding') return `${Number(d.count).toLocaleString()} payments · total ${usd(Number(d.total), 2)} over ${d.window_days}d`;
  if (tab === 'Order history') return `${Number(d.count).toLocaleString()} orders · ${d.note}`;
  if (tab === 'Extras') return 'fee tier & 30d volume · vault equities · staking';
  return '';
}

function FillsTable({ d }: { d: Awaited<ReturnType<typeof getExplorerFills>> }) {
  if (d.fills.length === 0) return <div className="empty">No fills in the window.</div>;
  return (
    <table className="xp-tbl">
      <thead><tr><th>Time</th><th>Coin</th><th>Dir</th><th className="r">Price</th><th className="r">Size</th>
        <th className="r">Value</th><th className="r">Closed PnL</th><th className="r">Fee</th><th>Role</th></tr></thead>
      <tbody>
        {[...d.fills].reverse().map((f, i) => (
          <tr key={i}>
            <td className="dim">{relAge(f.time / 1000)}</td>
            <td style={{ fontWeight: 600 }}>{f.coin}</td>
            <td><span className={`xp-side ${/buy|long/i.test(f.dir) ? 'long-c' : 'short-c'}`}>{f.dir}</span></td>
            <td className="r mono">{px(f.px)}</td>
            <td className="r">{amt(f.sz)}</td>
            <td className="r">{usd(f.px * f.sz, 2)}</td>
            <td className={`r ${sgn(f.closed_pnl)}`}>{f.closed_pnl ? usd(f.closed_pnl, 2) : '—'}</td>
            <td className="r dim">{f.fee != null ? `${f.fee} ${f.fee_token || ''}` : '—'}</td>
            <td className="dim">{f.crossed == null ? '—' : f.crossed ? 'taker' : 'maker'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const LEDGER_CHIP: Record<string, string> = {
  deposit: 'long', withdraw: 'short', accountClassTransfer: 'flat',
  internalTransfer: 'flat', spotTransfer: 'flat', subAccountTransfer: 'flat',
  vaultCreate: 'flat', vaultDeposit: 'flat', vaultWithdraw: 'flat',
  liquidation: 'short',
};
function LedgerTable({ d }: { d: Awaited<ReturnType<typeof getExplorerLedger>> }) {
  if (d.entries.length === 0) return <div className="empty">No deposits, withdrawals or transfers in the window.</div>;
  return (
    <table className="xp-tbl">
      <thead><tr><th>Time</th><th>Type</th><th className="r">USDC</th><th>Detail</th></tr></thead>
      <tbody>
        {[...d.entries].reverse().map((e, i) => {
          const raw = (e.raw || {}) as Record<string, unknown>;
          const liq = e.type === 'liquidation' ? raw : null;
          return (
            <tr key={i}>
              <td className="dim">{relAge(e.time / 1000)}</td>
              <td><span className={`tag ${LEDGER_CHIP[e.type] || 'flat'}`}>{e.type}</span></td>
              <td className={`r ${sgn(e.usdc)}`}>{usd(e.usdc, 2)}</td>
              <td className="dim">
                {liq
                  ? `liquidated notional ${usd(Number(liq.liquidatedNtlPos ?? liq.liquidated_ntl_pos ?? 0), 2)}${liq.liquidatedPositions ? ` · ${(liq.liquidatedPositions as unknown[]).length} position(s)` : ''}`
                  : (raw.destination ? String(raw.destination).slice(0, 10) + '…' : '')}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FundingTable({ d }: { d: Awaited<ReturnType<typeof getExplorerFunding>> }) {
  if (d.entries.length === 0) return <div className="empty">No funding payments in the window.</div>;
  const paid = Object.values(d.total_by_coin).filter((v) => v < 0).reduce((a, b) => a + b, 0);
  const recv = Object.values(d.total_by_coin).filter((v) => v > 0).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="sub" style={{ marginBottom: 6 }}>
        received {usd(recv, 2)} · paid {usd(paid, 2)} · by coin: {Object.entries(d.total_by_coin).map(([c, v]) => `${c} ${usd(v, 2)}`).join(' · ')}
      </div>
      <table className="xp-tbl">
        <thead><tr><th>Time</th><th>Coin</th><th className="r">Paid / received</th><th className="r">Position</th><th className="r">Rate</th></tr></thead>
        <tbody>
          {[...d.entries].reverse().map((e, i) => (
            <tr key={i}>
              <td className="dim">{relAge(e.time / 1000)}</td>
              <td style={{ fontWeight: 600 }}>{e.coin}</td>
              <td className={`r ${sgn(e.usdc)}`}>{usd(e.usdc, 4)}</td>
              <td className="r">{e.szi != null ? amt(e.szi) : '—'}</td>
              <td className="r dim">{e.rate != null ? `${(e.rate * 100).toFixed(4)}%` : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function statusChip(s: string | null): string {
  if (!s) return 'flat';
  const v = s.toLowerCase();
  if (v.includes('filled')) return 'long';
  if (v.includes('reject') || v.includes('canceled') || v.includes('cancelled')) return 'short';
  return 'flat';
}
function OrdersTable({ d }: { d: Awaited<ReturnType<typeof getExplorerOrders>> }) {
  if (d.orders.length === 0) return <div className="empty">No historical orders returned by the venue.</div>;
  return (
    <table className="xp-tbl">
      <thead><tr><th>Time</th><th>Coin</th><th>Side</th><th>Type</th><th className="r">Limit</th>
        <th className="r">Trigger</th><th className="r">Size</th><th>Status</th></tr></thead>
      <tbody>
        {d.orders.map((o, i) => (
          <tr key={i}>
            <td className="dim">{o.timestamp ? relAge(o.timestamp / 1000) : '—'}</td>
            <td style={{ fontWeight: 600 }}>{o.coin}</td>
            <td><span className={`xp-side ${o.side === 'B' ? 'long-c' : 'short-c'}`}>{o.side === 'B' ? 'buy' : 'sell'}</span></td>
            <td className="dim">{o.order_type}{o.is_trigger ? ' · trigger' : ''}{o.reduce_only ? ' · RO' : ''}</td>
            <td className="r mono">{px(o.limit_px)}</td>
            <td className="r mono">{px(o.trigger_px)}</td>
            <td className="r">{amt(o.orig_sz ?? o.sz)}</td>
            <td><span className={`tag ${statusChip(o.status)}`} title={o.status || ''}>{o.status || '—'}</span></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExtrasPanel({ d }: { d: Awaited<ReturnType<typeof getExplorerExtras>> }) {
  const vol30 = d.fees?.daily_volume_14d?.reduce((a, r) => a + (r.user_cross || 0) + (r.user_add || 0), 0) ?? null;
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div>
        <b style={{ fontSize: 13 }}>Fee tier & volume</b>
        {d.fees ? (
          <div className="sub">
            taker {d.fees.cross_rate != null ? `${(d.fees.cross_rate * 100).toFixed(4)}%` : '—'} ·
            maker {d.fees.add_rate != null ? `${(d.fees.add_rate * 100).toFixed(4)}%` : '—'} ·
            volume over the {d.fees.daily_volume_14d.length} returned days {vol30 != null ? usd(vol30) : '—'}
          </div>
        ) : <div className="sub">Not available.</div>}
      </div>
      <div>
        <b style={{ fontSize: 13 }}>Vault equities</b>
        {d.vault_equities && d.vault_equities.length > 0 ? (
          <div className="xp-scroll"><table className="xp-tbl">
            <thead><tr><th>Vault</th><th className="r">Equity</th></tr></thead>
            <tbody>{d.vault_equities.map((v, i) => (
              <tr key={i}><td className="mono">{v.vault ? shorten(v.vault) : '—'}</td><td className="r">{usd(v.equity, 2)}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <div className="sub">{d.vault_equities ? 'None.' : 'Not available.'}</div>}
      </div>
      <div>
        <b style={{ fontSize: 13 }}>Staking</b>
        {d.staking && d.staking.length > 0 ? (
          <div className="xp-scroll"><table className="xp-tbl">
            <thead><tr><th>Validator</th><th className="r">HYPE</th><th className="r">Locked until</th></tr></thead>
            <tbody>{d.staking.map((s, i) => (
              <tr key={i}><td className="mono">{s.validator ? shorten(s.validator) : '—'}</td>
                <td className="r">{amt(s.amount)}</td>
                <td className="r dim">{s.locked_until ? new Date(s.locked_until).toLocaleDateString() : '—'}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <div className="sub">{d.staking ? 'None.' : 'Not available.'}</div>}
      </div>
    </div>
  );
}

function ActivityTable({ addr }: { addr: string }) {
  const q = useQuery({
    queryKey: ['explorer-activity', addr],
    queryFn: () => getExplorerActivity(addr),
    staleTime: 300_000,
    retry: 1,
  });
  if (q.isLoading) return <Skel h={180} mb={0} />;
  if (q.isError || !q.data) {
    return <div className="empty">Activity unavailable. <button className="btn sm" onClick={() => q.refetch()}>Retry</button></div>;
  }
  const d = q.data;
  return (
    <>
      <div className="sub" style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
        <span>{d.count.toLocaleString()} events · last {d.window_days}d · HL ledger + Perpl indexed events
          {d.hl_note ? ` · ${d.hl_note}` : ''}{d.perpl_note ? ` · ${d.perpl_note}` : ''}</span>
        <Stamp at={d.fetched_at} tip="one venue-tagged event model — HL userNonFundingLedgerUpdates + perpl_events" />
      </div>
      {d.events.length === 0 ? (
        <div className="empty">No activity from either venue in the window.</div>
      ) : (
        <div className="xp-scroll" style={{ maxHeight: 460, overflowY: 'auto' }}>
          <table className="xp-tbl">
            <thead><tr><th>Time</th><th>Venue</th><th>Type</th><th className="r">Market</th><th className="r">Amount</th><th className="r">Fee</th></tr></thead>
            <tbody>
              {d.events.map((e, i) => (
                <tr key={i}>
                  <td className="dim">{relAge(e.at)}</td>
                  <td><span className={`tag ${e.venue === 'hl' ? 'accent' : 'flat'}`}>{e.venue === 'hl' ? 'HL' : 'Perpl'}</span></td>
                  <td className="dim">{e.type}</td>
                  <td className="r dim">{e.market_id ?? '—'}</td>
                  <td className={`r ${sgn(e.amount)}`}>{e.amount != null ? usd(e.amount, 2) : '—'}</td>
                  <td className="r dim">{e.fee != null ? usd(e.fee, 4) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}


// ===========================================================================
// Perpl section — same layout, second venue block below HL
// ===========================================================================

function PerplSection({ addr, q }: { addr: string; q: ReturnType<typeof useQuery<ExplorerPerpl>> }) {
  const d = q.data as ExplorerPerpl | undefined;
  const histQ = useQuery({
    queryKey: ['explorer-perpl-history', addr],
    queryFn: () => getExplorerPerplHistory(addr),
    staleTime: 120_000,
    retry: 1,
  });
  const hist = histQ.data;

  if (q.isLoading) return <><div className="xp-venue-h"><h2>Perpl</h2></div><Skel h={140} mb={0} /></>;
  if (q.isError) {
    const st = (q.error as { response?: { status?: number } })?.response?.status;
    return (
      <>
        <div className="xp-venue-h"><h2>Perpl</h2></div>
        <div className="card"><div className="empty">
          {st === 503 ? 'Monad RPC busy — retry in ~30s.' : 'Perpl lookup failed.'}{' '}
          <button className="btn sm" onClick={() => q.refetch()}>Retry</button>
        </div></div>
      </>
    );
  }
  if (!d) return null;
  const lb = d.leaderboard;
  const positions = d.positions ?? [];
  const tpsl = (mid: number) => (d.orders ?? []).filter((o) => o.market_id === mid && (o.kind === 'stop_loss' || o.kind === 'take_profit'));

  if (!d.perpl_account) {
    return (
      <>
        <div className="xp-venue-h"><h2>Perpl</h2></div>
        <div className="card"><div className="empty">
          No Perpl trading account on-chain for this address (getAccountByAddr = 0).
        </div></div>
      </>
    );
  }

  const allRank = lb.ranks.all_vol || lb.ranks.all_pnl;
  const dayRank = lb.ranks.day_pnl;
  const snapPts: Pt[] = (lb.snapshot_history || []).map((h) => ({ t: Date.parse(h.t + 'Z') / 1000, v: h.pnl }));

  return (
    <>
      <div className="xp-venue-h">
        <h2>Perpl · account #{d.account_id}</h2>
        {lb.ranks.all_vol && (
          <span className="xp-tag" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
            all-time volume #{lb.ranks.all_vol.rank}
          </span>
        )}
        {hist?.indexer && (
          <span className="xp-tag soft" title={hist.provenance}>
            {hist.indexed_from
              ? `history indexed from ${new Date(hist.indexed_from + 'Z').toLocaleDateString()}${hist.indexer.backfill_pct != null ? ` · ${hist.indexer.backfill_pct}% backfilled` : ''}`
              : hist.indexer.backfill_pct != null
                ? `no indexed events yet · chain ${hist.indexer.backfill_pct}% backfilled`
                : (hist.indexer.note && hist.indexer.note !== 'ok'
                  ? hist.indexer.note : 'history index starting')}
          </span>
        )}
      </div>

      {/* Perpl stat cards — leaderboard windows; honest when absent */}
      {lb.on_leaderboard ? (
        <div className="xp-stats">
          <div className="xp-stat" title="Perpl leaderboard, all-time window">
            <div className="lbl">All-time PnL</div>
            <div className={`val ${sgn(lb.ranks.all_pnl?.pnl)}`}>{usd(lb.ranks.all_pnl?.pnl, 2)}</div>
          </div>
          <div className="xp-stat" title="Perpl leaderboard, 24h window">
            <div className="lbl">24h PnL</div>
            <div className={`val ${sgn(dayRank?.pnl)}`}>{usd(dayRank?.pnl, 2)}</div>
          </div>
          <div className="xp-stat"><div className="lbl">All-time volume</div>
            <div className="val">{usd(lb.ranks.all_vol?.volume)}</div></div>
          <div className="xp-stat"><div className="lbl">All-time ROI</div>
            <div className={`val ${sgn(lb.ranks.all_pnl?.roi)}`}>{lb.ranks.all_pnl?.roi != null ? pct(lb.ranks.all_pnl.roi) : 'Not available'}</div></div>
          <div className="xp-stat"><div className="lbl">Rank</div>
            <div className="val">{allRank ? `#${allRank.rank}` : 'Not ranked'}</div></div>
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="sub">Not on the Perpl leaderboard right now — live on-chain state only. {lb.snapshot_note}</div>
        </div>
      )}

      {/* Perpl equity — snapshot history where we have it */}
      {snapPts.length >= 2 ? (
        <div className="card xp-eq">
          <div className="top">
            <span style={{ fontSize: 13, fontWeight: 600 }}>Equity <span className="dim" style={{ fontWeight: 400 }}>
              · all-time PnL across this wallet's top-20 snapshot appearances</span></span>
            <Stamp at={d.fetched_at} tip={lb.snapshot_note} />
          </div>
          <EquityChart pts={snapPts} />
          <div className="sub">{lb.snapshot_appearances.toLocaleString()} appearances since {lb.snapshot_since ? new Date(lb.snapshot_since + 'Z').toLocaleDateString() : '—'}</div>
        </div>
      ) : hist && hist.equity.length >= 2 ? (
        <div className="card xp-eq">
          <div className="top">
            <span style={{ fontSize: 13, fontWeight: 600 }}>Equity <span className="dim" style={{ fontWeight: 400 }}>· {hist.equity_label}</span></span>
            <Stamp at={hist.fetched_at} tip={hist.provenance} />
          </div>
          <EquityChart pts={hist.equity.map((e) => ({ t: Date.parse(e.t + 'Z') / 1000, v: e.v }))} />
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="empty">No Perpl equity series yet — this wallet has no top-20 snapshot history and the on-chain index has no balance events for it.</div>
        </div>
      )}

      {/* Perpl donuts */}
      <div className="xp-donuts">
        <div className="xp-donut">
          <Donut slices={perplShares(positions).map((s, i) => ({ pct: s.share, color: SLICE_COLORS[i % SLICE_COLORS.length] }))} />
          <div>
            <div className="lbl">Perps position value</div>
            <div className="big">{usd(positions.reduce((a, x) => a + Math.abs(x.notional || 0), 0))}</div>
            <div className="cap">
              {positions.length} position{positions.length === 1 ? '' : 's'}
              {perplShares(positions).slice(0, 3).map((s, i) => (
                <span key={s.symbol}>{' · '}<span style={{ color: SLICE_COLORS[i % SLICE_COLORS.length] }}>{s.symbol} {s.share.toFixed(0)}%</span></span>
              ))}
            </div>
          </div>
        </div>
        <div className="xp-donut">
          <Donut slices={(d.balance ?? 0) > 0 ? [{ pct: Math.min(100, ((d.margin_used ?? 0) / (d.balance || 1)) * 100), color: 'var(--accent)' }] : []} />
          <div>
            <div className="lbl">Account balance</div>
            <div className="big">{usd(d.balance, 2)}</div>
            <div className="cap">Margin used {usd(d.margin_used, 2)}</div>
          </div>
        </div>
        <div className="xp-donut">
          <Donut slices={(d.balance ?? 0) > 0 ? [{
            pct: Math.max(0, Math.min(100, 100 - (((d.balance ?? 0) - (d.margin_used ?? 0)) / (d.balance || 1)) * 100)),
            color: (d.balance ?? 0) - (d.margin_used ?? 0) <= 0 ? 'var(--short)' : 'var(--accent)',
          }] : []} />
          <div>
            <div className="lbl">Free margin</div>
            <div className="big">{usd((d.balance ?? 0) - (d.margin_used ?? 0), 2)}</div>
            <div className={`cap ${(d.balance ?? 0) - (d.margin_used ?? 0) <= 0 ? 'short-c' : ''}`}>
              {(d.balance ?? 0) <= 0 ? 'No collateral on-chain'
                : (d.balance ?? 0) - (d.margin_used ?? 0) <= 0 ? 'margin fully used'
                  : `${((((d.balance ?? 0) - (d.margin_used ?? 0)) / (d.balance || 1)) * 100).toFixed(1)}% free`}
            </div>
          </div>
        </div>
      </div>

      {/* Perpl positions + orders panel */}
      <div className="card xp-panel">
        <div className="xp-scroll">
          <table className="xp-tbl">
            <thead><tr>
              <th>Token</th><th>Side</th><th className="r">Size</th><th className="r">Value</th><th className="r">Entry</th>
              <th className="r">Mark</th><th className="r">Liq price <span className="dim">(formula)</span></th>
              <th className="r">uPnL</th><th className="r">Funding</th><th>TP/SL</th><th className="r">Lev</th>
            </tr></thead>
            <tbody>
              {positions.length === 0 ? (
                <tr><td colSpan={11} className="dim" style={{ padding: 14 }}>No open Perpl positions.</td></tr>
              ) : positions.map((p, i) => {
                const t = tpsl(p.market_id);
                const sl = t.find((o) => o.kind === 'stop_loss');
                const tp = t.find((o) => o.kind === 'take_profit');
                return (
                  <tr key={i}>
                    <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                    <td><span className={`xp-side ${p.side === 'long' ? 'long-c' : 'short-c'}`}>
                      {p.side === 'long' ? 'Long' : 'Short'}</span></td>
                    <td className="r">{amt(p.size)}</td>
                    <td className="r">{usd(p.notional, 2)}</td>
                    <td className="r mono">{px(p.entry_price)}</td>
                    <td className="r mono">{px(p.mark_price)}</td>
                    <td className="r mono short-c"
                      title={`${p.liq_source} — liq = entry ∓ (deposit − MMR)/size, with the market's LIVE maintenanceMarginHdths read on this request`}>
                      {px(p.liq_price)}
                    </td>
                    <td className={`r ${sgn(p.pnl)}`} style={{ fontWeight: 600 }}>{usd(p.pnl, 2)}</td>
                    <td className={`r ${sgn(p.funding_pnl)}`}>{usd(p.funding_pnl, 2)}</td>
                    <td className="dim">
                      {sl ? <>SL {px(sl.price)}</> : <span className="dim">no SL</span>}
                      {' · '}
                      {tp ? <>TP {px(tp.price)}</> : <span className="dim">no TP</span>}
                    </td>
                    <td className="r">{p.leverage}x</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {(d.orders ?? []).filter((o) => o.kind === 'limit').length > 0 && (
          <>
            <div className="sub" style={{ marginTop: 12, marginBottom: 4 }}>Resting limit orders</div>
            <div className="xp-scroll">
              <table className="xp-tbl">
                <thead><tr><th>Token</th><th>Type</th><th>Side</th><th className="r">Price</th><th className="r">Size</th><th className="r">Lev</th><th className="r">Margin locked</th></tr></thead>
                <tbody>
                  {(d.orders ?? []).filter((o) => o.kind === 'limit').map((o, i) => (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{o.symbol}</td>
                      <td className="dim">{o.order_type}</td>
                      <td className="dim">{o.side}</td>
                      <td className="r mono">{px(o.price)}</td>
                      <td className="r">{amt(o.size)}</td>
                      <td className="r">{o.leverage ? `${o.leverage}x` : '—'}</td>
                      <td className="r">{usd(o.margin_locked, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        <div className="xp-foot">
          <span>Perpl · on-chain state · as of {shortAge(d.fetched_at)}</span>
          <span>{hist?.status_line || 'Deposits, fills & funding appear in Activity as the index backfills'}</span>
        </div>
      </div>

      <PerplHistoryCard addr={addr} />
    </>
  );
}

function perplShares(positions: ExplorerPerpl['positions']) {
  const list = positions ?? [];
  const total = list.reduce((a, p) => a + Math.abs(p.notional || 0), 0);
  if (!total) return [];
  return list
    .map((p) => ({ symbol: p.symbol, share: (Math.abs(p.notional || 0) / total) * 100 }))
    .sort((a, b) => b.share - a.share);
}

function PerplHistoryCard({ addr }: { addr: string }) {
  const q = useQuery({
    queryKey: ['explorer-perpl-history', addr],
    queryFn: () => getExplorerPerplHistory(addr),
    staleTime: 120_000,
    retry: 1,
  });
  if (q.isLoading) return <Skel h={140} mb={0} />;
  if (q.isError || !q.data) {
    return <div className="card" style={{ marginTop: 14 }}><div className="empty">
      Indexed history unavailable. <button className="btn sm" onClick={() => q.refetch()}>Retry</button>
    </div></div>;
  }
  const d = q.data;
  return (
    <div className="card xp-panel" style={{ marginTop: 14 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <b style={{ fontSize: 13 }}>Perpl history <span className="dim" style={{ fontWeight: 400 }}>· decoded on-chain events</span></b>
        <Stamp at={d.fetched_at} tip={d.provenance} />
      </div>
      {d.status_line && <div className="sub" style={{ marginBottom: 6 }}>{d.status_line}</div>}
      {d.count === 0 ? (
        <div className="empty">
          {d.indexer && (d.indexer.note || '').startsWith('awaiting')
            ? 'The on-chain indexer is idle awaiting its Envio API token — nothing indexed yet.'
            : 'No indexed events for this address in the blocks covered so far.'}
        </div>
      ) : (
        <div className="xp-scroll" style={{ maxHeight: 420, overflowY: 'auto' }}>
          <table className="xp-tbl">
            <thead><tr><th>Time</th><th>Event</th><th className="r">Market</th><th className="r">Amount</th>
              <th className="r">Fee</th><th className="r">Balance after</th><th>Tx</th></tr></thead>
            <tbody>
              {d.events.map((e, i) => (
                <tr key={i}>
                  <td className="dim">{relAge(e.at)}</td>
                  <td title={(e as { via_tx_from?: boolean }).via_tx_from
                    ? 'sent by this address (no account attribution — may include batch-operator events)'
                    : e.attributed ? 'account attributed from same-tx context / tx sender' : ''}>
                    {e.event_type}{(e as { via_tx_from?: boolean }).via_tx_from ? ' *' : e.attributed ? ' ·' : ''}
                  </td>
                  <td className="r dim">{e.market_id ?? '—'}</td>
                  <td className={`r ${sgn(e.bfa ?? e.amount)}`}>{usd(e.bfa ?? e.amount, 2)}</td>
                  <td className="r dim">{e.fee != null ? usd(e.fee, 4) : '—'}</td>
                  <td className="r">{usd(e.balance_after, 2)}</td>
                  <td className="mono dim" title={e.tx}>{e.tx ? `${e.tx.slice(0, 8)}…` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
