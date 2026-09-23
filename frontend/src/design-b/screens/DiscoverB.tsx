import { useState, useMemo, useEffect } from 'react';
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useTraders, useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import Sparkline from '@/components/copy/Sparkline';
import { getEquityBatch, getExchanges, type ExchangeInfo, type TraderListItem, type EquityTimeframe } from '@/lib/copyApi';
import { useCopyStore } from '@/stores/copyStore';
import { COPY_LIVE_ENABLED } from '@/config/constants';
import { formatCompact, formatPercent, shortenAddress } from '@/lib/formatters';

// Design B — Copy trade Discover (spec §3.1). Presentation only: the EXACT
// existing data layer from pages/copy/Discover.tsx (same hooks, same query
// params, same filter state/handlers). Copy opens the existing
// StartPaperCopyModal UNCHANGED; Profile navigates to the trader-profile screen
// (§3.2 — "there is no other way to reach a profile"). Nothing money-path is
// touched (wrapper Rule 7).

type ViewMode = 'list' | 'card';

const sortsFor = (exchange: string): { key: string; label: string; title: string }[] => [
  { key: 'pnl', label: 'Top profit', title: 'Ranked by leaderboard PnL for the selected window' },
  { key: 'vol', label: 'Volume', title: 'Ranked by traded volume for the selected window' },
  { key: 'active', label: 'Most active', title: 'Days active (last 7) then 24h volume velocity — derived from leaderboard snapshots (30-min on Perpl, hourly on HL)' },
  ...(exchange === 'hl' ? [
    { key: 'consistent', label: 'Consistent', title: 'Profitable in the most leaderboard windows simultaneously, then week-PnL trend' },
    { key: 'win_rate', label: 'Win rate', title: 'Last 7 days, from venue fill history; needs ≥20 closed trades — smaller samples sort below' },
  ] : []),
  { key: 'recent_activity', label: 'Recently active', title: 'Most recent detected fill first' },
];

const WINDOW_LABELS: Record<string, string> = { all: 'All', day: '24h', week: '1W', month: '1M' };

const DEFAULT_EXCHANGES: ExchangeInfo[] = [
  { id: 'perpl', label: 'Perpl', periods: ['all', 'day'], has_live_positions: true, copy_execution: true },
];

function relAge(iso: string | null | undefined): string {
  if (!iso) return 'recently';
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z')) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function medalClass(rank: number): string { return rank === 1 ? 'medal m1' : rank === 2 ? 'medal m2' : rank === 3 ? 'medal m3' : 'medal'; }

function Badges({ t, exchange }: { t: TraderListItem; exchange: string }) {
  const days = t.activity?.active_days_7d;
  const trades7 = t.fill_stats?.trades_7d;
  return (
    <span className="badges">
      {exchange === 'hl' && <span className="tag long">HL</span>}
      {days != null && <span className="tag accent">{days} of 7 days</span>}
      {exchange === 'hl' && trades7 != null && trades7 > 0 && (
        <span className="tag flat">~{Math.max(1, Math.round(trades7 / 7))} trades/day</span>
      )}
    </span>
  );
}

export default function DiscoverB() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const sort = useCopyStore((s) => s.discoverSort);
  const setSort = useCopyStore((s) => s.setDiscoverSort);
  const [period, setPeriod] = useState('all');
  const [openOnly, setOpenOnly] = useState(false);
  const [searchParams] = useSearchParams();
  const walletFilter = useMemo(() => {
    const ws = (searchParams.get('wallets') || '').split(',')
      .map((w) => w.trim().toLowerCase())
      .filter((w) => /^0x[0-9a-f]{40}$/.test(w));
    return ws.length ? ws : null;
  }, [searchParams]);
  const filterAsset = searchParams.get('asset');
  const [exchanges, setExchanges] = useState<ExchangeInfo[]>(DEFAULT_EXCHANGES);
  const [exchange, setExchange] = useState(() =>
    (new URLSearchParams(window.location.search).has('wallets') ? 'hl' : 'perpl'));
  useEffect(() => { if (walletFilter) setExchange('hl'); }, [walletFilter]);
  useEffect(() => { getExchanges().then((xs) => { if (xs?.length) setExchanges(xs); }).catch(() => {}); }, []);
  const exchInfo = exchanges.find((x) => x.id === exchange) ?? exchanges[0];
  const periods = exchInfo?.periods ?? ['all', 'day'];
  useEffect(() => {
    if (!periods.includes(period)) setPeriod('all');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange]);
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem('perpl-copy-view') as ViewMode) || 'list');
  useEffect(() => { localStorage.setItem('perpl-copy-view', view); }, [view]);
  const { traders, loading, error } = useTraders({
    sort, period, limit: exchange === 'hl' ? 50 : 24, exchange,
    wallets: walletFilter ? walletFilter.join(',') : undefined,
  });
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);

  const SORTS = sortsFor(exchange);
  useEffect(() => {
    if (!SORTS.some((s) => s.key === sort)) setSort('pnl');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange]);
  const [activeProfitable, setActiveProfitable] = useState(false);
  const [search, setSearch] = useState('');
  const [copyTarget, setCopyTarget] = useState<TraderListItem | null>(null);

  const onWatch = async (w: string) => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(w, exchange)) await watchlist.unwatch(w, exchange);
    else await watchlist.watch(w, exchange);
  };
  const openProfile = (w: string) =>
    navigate(`/copy/trader/${w}${exchange === 'hl' ? '?exchange=hl' : ''}`);

  const equityTf: EquityTimeframe = period === 'day' ? '24h' : 'all';
  const [equity, setEquity] = useState<Record<string, number[]>>({});
  const walletKey = traders.map((t) => t.wallet_address).join(',');
  useEffect(() => {
    const wallets = traders.map((t) => t.wallet_address);
    if (!wallets.length) return;
    let cancelled = false;
    getEquityBatch(wallets, equityTf, 60, exchange)
      .then((d) => { if (!cancelled) setEquity((prev) => ({ ...prev, ...d })); })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walletKey, equityTf, exchange]);

  const shown = useMemo(() => {
    if (walletFilter) {
      const set = new Set(walletFilter);
      return traders.filter((t) => set.has(t.wallet_address.toLowerCase()));
    }
    let rows = openOnly ? traders.filter((t) => t.has_active_positions || t.active_positions_pending) : traders;
    if (activeProfitable && exchange === 'hl') {
      rows = rows.filter((t) =>
        (t.activity?.active_days_7d ?? 0) >= 5
        && ((t.activity?.consistency_flags ?? 0) & 2) !== 0
        && (t.fill_stats?.win_rate_7d == null || t.fill_stats.win_rate_7d >= 0.5));
    }
    const q = search.trim().toLowerCase();
    if (q) {
      rows = rows.filter((t) =>
        t.wallet_address.toLowerCase().includes(q)
        || (t.display_name ?? '').toLowerCase().includes(q));
    }
    return rows;
  }, [openOnly, activeProfitable, exchange, traders, search, walletFilter]);

  const sourceLabel = exchInfo?.label ?? (exchange === 'hl' ? 'Hyperliquid' : 'Perpl');
  const TABS = [
    { key: 'discover', label: 'Discover', to: '/copy/discover' },
    { key: 'watchlist', label: 'Watchlist', to: '/copy/watchlist' },
    { key: 'dashboard', label: 'Dashboard', to: '/copy/dashboard' },
    { key: 'portfolio', label: 'Portfolio', to: '/copy/portfolio' },
    { key: 'history', label: 'History', to: '/copy/history' },
  ];
  const activeTab = TABS.find((t) => location.pathname.startsWith(t.to))?.key ?? 'discover';

  return (
    <div className="screen">
      <div className="head">
        <div>
          <h1>Copy trade</h1>
          <p>Pick a trader, mirror their orders on Perpl, confirm each one yourself</p>
        </div>
        <span className="tag amber">
          {COPY_LIVE_ENABLED ? 'Live copy on — you confirm every order' : 'Live copy off. No real orders placed.'}
        </span>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} aria-selected={activeTab === t.key} onClick={() => navigate(t.to)}>{t.label}</button>
        ))}
      </div>

      <div className="filters">
        {walletFilter && (
          <div className="dlbanner">
            <span className="msg">
              Showing {shown.length} wallet{shown.length === 1 ? '' : 's'} that hold{shown.length === 1 ? 's' : ''}
              {filterAsset ? ` ${filterAsset}` : ' this asset'} (from Analytics)
            </span>
            {!loading && shown.length < walletFilter.length && (
              <span className="note" title="These addresses are in no stored leaderboard batch right now.">
                {walletFilter.length - shown.length} linked wallet{walletFilter.length - shown.length === 1 ? '' : 's'} not in any stored batch
              </span>
            )}
            <button className="btn sm primary" style={{ marginLeft: 'auto' }} onClick={() => navigate('/copy/discover', { replace: true })}>Show all traders</button>
          </div>
        )}
        {/* Row 1 */}
        <div className="frow">
          <div className="seg">
            {exchanges.map((x) => (
              <button key={x.id} aria-pressed={exchange === x.id} onClick={() => setExchange(x.id)}>{x.label}</button>
            ))}
          </div>
          <label className="search">
            <span aria-hidden>⌕</span>
            <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search trader or address" />
          </label>
          <div className="seg">
            <button aria-pressed={view === 'list'} title="List view" onClick={() => setView('list')}>List</button>
            <button aria-pressed={view === 'card'} title="Card view" onClick={() => setView('card')}>Cards</button>
          </div>
        </div>
        {/* Row 2 */}
        <div className="frow">
          <div className="fgroup">
            <span className="flabel">Sort by</span>
            <div className="seg">
              {SORTS.map((s) => (
                <button key={s.key} title={s.title} aria-pressed={sort === s.key} onClick={() => setSort(s.key)}>{s.label}</button>
              ))}
            </div>
          </div>
          <div className="fgroup">
            <span className="flabel">Window</span>
            <div className="seg">
              {periods.map((pk) => (
                <button key={pk} aria-pressed={period === pk} onClick={() => setPeriod(pk)}>{WINDOW_LABELS[pk] ?? pk}</button>
              ))}
            </div>
          </div>
          <div className="fgroup">
            <span className="flabel">Only show</span>
            <div className="chips">
              {exchange === 'hl' && (
                <button className="chipbtn" aria-pressed={activeProfitable}
                  title="active ≥5 of last 7 days AND profitable this week AND (win rate ≥50% or not yet measurable)"
                  onClick={() => setActiveProfitable((v) => !v)}>Active and profitable</button>
              )}
              {exchInfo?.has_live_positions !== false && (
                <button className="chipbtn" aria-pressed={openOnly} onClick={() => setOpenOnly((v) => !v)}>Has open trades</button>
              )}
            </div>
          </div>
        </div>
      </div>

      {!loading && (
        <p className="count">{shown.length} trader{shown.length === 1 ? '' : 's'} on {sourceLabel}, showing {view} view</p>
      )}

      {error && <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)' }}>{error}</div>}

      {loading && traders.length === 0 ? (
        <div className="empty">Loading traders…</div>
      ) : shown.length === 0 ? (
        <div className="empty">{openOnly ? 'No traders with open trades right now.' : 'No traders found.'}</div>
      ) : view === 'list' ? (
        <div className="ltable">
          <table>
            <thead>
              <tr>
                <th style={{ width: 40 }}>#</th><th>Trader</th>
                <th className="r">Profit</th><th className="r">Return</th><th className="r">Volume</th>
                <th className="r">Open</th><th>Equity {period === 'day' ? '24h' : 'all'}</th><th />
              </tr>
            </thead>
            <tbody>
              {shown.map((t) => {
                const openCount = t.active_positions_pending ? '…' : (t.active_positions_count ?? 0);
                return (
                  <tr key={t.wallet_address}>
                    <td><span className={medalClass(t.rank)}>{t.rank}</span></td>
                    <td>
                      <div className="name">
                        {t.display_name || shortenAddress(t.wallet_address)}
                        <Badges t={t} exchange={exchange} />
                      </div>
                      <div className="meta">{shortenAddress(t.wallet_address)} · active {relAge(t.activity?.last_active_at || t.last_fill_at)}</div>
                    </td>
                    <td className="r"><span className={`pv ${t.pnl_total >= 0 ? 'long-c' : 'short-c'}`}>{formatCompact(t.pnl_total)}</span></td>
                    <td className="r"><span className={`roi ${t.roi >= 0 ? 'long-c' : 'short-c'}`}>{formatPercent(t.roi)}</span></td>
                    <td className="r">{formatCompact(t.volume)}</td>
                    <td className="r">{openCount}</td>
                    <td><span className="spark"><Sparkline points={equity[t.wallet_address.toLowerCase()]} width={110} height={26} /></span></td>
                    <td>
                      <div className="rowacts">
                        <button className="btn sm ghost" onClick={() => openProfile(t.wallet_address)}>Profile</button>
                        <button className="btn sm" aria-pressed={watchlist.isWatched(t.wallet_address, exchange)} onClick={() => onWatch(t.wallet_address)}>
                          {watchlist.isWatched(t.wallet_address, exchange) ? 'Watching' : 'Watch'}
                        </button>
                        <button className="btn sm primary" onClick={() => setCopyTarget(t)}>Copy</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="cards">
          {shown.map((t) => {
            const openCount = t.active_positions_pending ? '…' : (t.active_positions_count ?? 0);
            const chips = t.active_markets?.slice(0, 3) ?? [];
            return (
              <div className="tc" key={t.wallet_address}>
                <div className="top">
                  <span className={medalClass(t.rank)}>{t.rank}</span>
                  <div>
                    <div className="name">{t.display_name || shortenAddress(t.wallet_address)}</div>
                    <div className="meta">Active {relAge(t.activity?.last_active_at || t.last_fill_at)}</div>
                  </div>
                </div>
                <Badges t={t} exchange={exchange} />
                <div className="pnl">
                  <div className="v">{formatCompact(t.pnl_total)}<small>Total profit</small></div>
                  <div className={`roi ${t.roi >= 0 ? 'long-c' : 'short-c'}`}>{formatPercent(t.roi)}</div>
                </div>
                <div className="facts">
                  <div><b>{formatCompact(t.volume)}</b><span>Volume</span></div>
                  <div><b>{openCount}</b><span>Open</span></div>
                </div>
                <div className="openpos">
                  {chips.length ? chips.map((m, i) => (
                    <span key={i} className={`tag ${m.side === 'long' ? 'long' : 'short'}`}>{m.symbol} {m.side} {m.leverage ? `${m.leverage}x` : ''}</span>
                  )) : <Sparkline points={equity[t.wallet_address.toLowerCase()]} width={200} height={28} />}
                </div>
                <div className="acts">
                  <button className="btn sm ghost" onClick={() => openProfile(t.wallet_address)}>Profile</button>
                  <button className="btn sm" aria-pressed={watchlist.isWatched(t.wallet_address, exchange)} onClick={() => onWatch(t.wallet_address)}>
                    {watchlist.isWatched(t.wallet_address, exchange) ? 'Watching' : 'Watch'}
                  </button>
                  <button className="btn sm primary" onClick={() => setCopyTarget(t)}>Copy</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {copyTarget && (
        <StartPaperCopyModal
          isOpen={!!copyTarget}
          onClose={() => setCopyTarget(null)}
          traderWallet={copyTarget.wallet_address}
          traderName={copyTarget.display_name}
          exchange={exchange}
          onSubmit={subs.create}
        />
      )}
    </div>
  );
}
