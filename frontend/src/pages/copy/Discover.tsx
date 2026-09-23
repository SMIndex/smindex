import { useState, useMemo, useEffect } from 'react';
import { clsx } from 'clsx';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useTraders, useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import TraderCard from '@/components/copy/TraderCard';
import TraderListRow, { LIST_GRID } from '@/components/copy/TraderListRow';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import TraderProfileModal from '@/components/copy/TraderProfileModal';
import { getEquityBatch, getExchanges, type ExchangeInfo, type TraderListItem, type EquityTimeframe } from '@/lib/copyApi';
import { useIsMobile } from '@/hooks/useIsMobile';
import { useCopyStore } from '@/stores/copyStore';
import MobileCopy from '@/components/copy/mobile/MobileCopy';

type ViewMode = 'list' | 'card';

// Sorts are exchange-dependent: Perpl snapshots carry only the cumulative
// all-time window, so 'Consistent' (needs day/week/month PnL) and 'Win rate'
// (fills-based, HL sampler) are underivable there — buttons hide, no dead
// filters. Landing default stays Top PnL (this pass changes no defaults).
const sortsFor = (exchange: string): { key: string; label: string; title: string }[] => [
  { key: 'pnl', label: 'Top PnL', title: 'Ranked by leaderboard PnL for the selected window' },
  { key: 'vol', label: 'Volume', title: 'Ranked by traded volume for the selected window' },
  { key: 'active', label: 'Most active', title: 'Days active (last 7) then 24h volume velocity — derived from leaderboard snapshots (30-min on Perpl, hourly on HL)' },
  ...(exchange === 'hl' ? [
    { key: 'consistent', label: 'Consistent', title: 'Profitable in the most leaderboard windows (day/week/month/all) simultaneously, then week-PnL trend — derived from leaderboard snapshots (30-min on Perpl, hourly on HL)' },
    { key: 'win_rate', label: 'Win rate', title: 'Last 7 days, from venue fill history; needs ≥20 closed trades — smaller samples sort below' },
  ] : []),
  { key: 'recent_activity', label: 'Recently active', title: 'Most recent detected fill first' },
];

// Window segment labels (mockup): All | 24h | 1W | 1M
const WINDOW_LABELS: Record<string, string> = { all: 'All', day: '24h', week: '1W', month: '1M' };

const PERIOD_LABELS: Record<string, string> = {
  all: 'All Time', day: '24h', week: 'Week', month: 'Month',
};

// Fallback while /api/exchanges loads (matches the backend's static registry)
const DEFAULT_EXCHANGES: ExchangeInfo[] = [
  { id: 'perpl', label: 'Perpl', periods: ['all', 'day'], has_live_positions: true, copy_execution: true },
];

function DesktopDiscover() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const sort = useCopyStore((s) => s.discoverSort);
  const setSort = useCopyStore((s) => s.setDiscoverSort);
  const [period, setPeriod] = useState('all');
  const [openOnly, setOpenOnly] = useState(false);
  // Tier-2 B3: ?wallets=0x..,0x.. (+&asset=BTC) from the analytics asset page.
  // REACTIVE: derived from the live search params so nav to /copy/discover
  // without params (the Copy Trade nav entry) always lands unfiltered, and
  // "Show all traders" is a plain navigation. In this mode the server is
  // asked for EXACTLY these addresses (board rank limit does not apply).
  const [searchParams] = useSearchParams();
  const walletFilter = useMemo(() => {
    const ws = (searchParams.get('wallets') || '').split(',')
      .map((w) => w.trim().toLowerCase())
      .filter((w) => /^0x[0-9a-f]{40}$/.test(w));
    return ws.length ? ws : null;
  }, [searchParams]);
  const filterAsset = searchParams.get('asset');
  // Exchange tabs are DATA from /api/exchanges — adding a venue is backend config.
  const [exchanges, setExchanges] = useState<ExchangeInfo[]>(DEFAULT_EXCHANGES);
  // analytics wallets are HL leaders — land on the HL tab when filtered
  const [exchange, setExchange] = useState(() =>
    (new URLSearchParams(window.location.search).has('wallets') ? 'hl' : 'perpl'));
  useEffect(() => { if (walletFilter) setExchange('hl'); }, [walletFilter]);
  useEffect(() => {
    getExchanges().then((xs) => { if (xs?.length) setExchanges(xs); }).catch(() => {});
  }, []);
  const exchInfo = exchanges.find((x) => x.id === exchange) ?? exchanges[0];
  const periods = exchInfo?.periods ?? ['all', 'day'];
  // keep the selected period valid when switching exchange
  useEffect(() => {
    if (!periods.includes(period)) setPeriod('all');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange]);
  const [view, setView] = useState<ViewMode>(() => (localStorage.getItem('perpl-copy-view') as ViewMode) || 'list');
  useEffect(() => { localStorage.setItem('perpl-copy-view', view); }, [view]);
  // HL serves the stored top-100 hourly batch; no pagination UI exists, so the
  // HL tab gets a higher flat limit (50) than the Perpl live passthrough (24).
  const { traders, loading, error } = useTraders({
    sort, period, limit: exchange === 'hl' ? 50 : 24, exchange,
    wallets: walletFilter ? walletFilter.join(',') : undefined,
  });
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);

  const SORTS = sortsFor(exchange);
  // keep the selected sort valid when switching exchange (no dead filters)
  useEffect(() => {
    if (!SORTS.some((s) => s.key === sort)) setSort('pnl');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exchange]);
  // 'Active + Profitable' quick filter (HL only — needs the week-PnL bit).
  // Predicate: active_days_7d >= 5 AND week-window profitable (flags bit1)
  // AND (win_rate >= 50% OR win_rate unknown).
  const [activeProfitable, setActiveProfitable] = useState(false);
  // Row-1 search: client-side over the loaded board (name or address substring)
  const [search, setSearch] = useState('');

  const [copyTarget, setCopyTarget] = useState<TraderListItem | null>(null);
  const onWatch = async (w: string) => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(w, exchange)) await watchlist.unwatch(w, exchange);
    else await watchlist.watch(w, exchange);
  };
  const [profileWallet, setProfileWallet] = useState<string | null>(null);
  const onProfile = (w: string) => setProfileWallet(w);
  void navigate;

  // Real equity sparkline series (leaderboard_snapshots) covering the SELECTED leaderboard
  // timeframe: "All Time" -> full history, "24h" -> last 24h. Fetched per visible wallet
  // set + timeframe; keyed on the (stable) wallet membership so polls don't refetch.
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
  // Memoized + stable: in "open trades only" mode, KEEP pending cards (their count
  // hasn't resolved yet) so they don't bounce in/out while summaries refresh.
  const shown = useMemo(() => {
    if (walletFilter) {
      // exact-address mode: the server returned exactly the linked wallets —
      // no other filter (openOnly/activeProfitable/search) shrinks the promise
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

  return (
    <CopyLayout>
      {/* Filters — approved grouped layout (owner mockup): Row 1 = exchange
          segment · search · view toggle; Row 2 = labeled groups "Sort by"
          (single-select pills) | "Window" (boxed segment) | "Filters"
          (check chips) with hairlines. */}
      <div className="mb-4 rounded-[14px] p-3" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        {/* Tier-2 B3: analytics deep-link banner — prominent, and the server
            returns EXACTLY the requested addresses (rows beyond the board's
            rank limit are fetched by address with real rank badges), so the
            list never silently drops a wallet the link promised. */}
        {walletFilter && (
          <div className="flex items-center gap-3 flex-wrap mb-3 px-3 py-2.5 rounded-[10px]"
               style={{ background: 'var(--accent-soft)', border: '1px solid var(--accent)' }}>
            <span className="text-[12.5px] font-semibold" style={{ color: 'var(--accent-2)' }}>
              Showing {shown.length} wallet{shown.length === 1 ? '' : 's'} that hold{shown.length === 1 ? 's' : ''}
              {filterAsset ? ` ${filterAsset}` : ' this asset'} (from Analytics)
            </span>
            {!loading && shown.length < walletFilter.length && (
              <span className="text-[11px]" style={{ color: 'var(--dim)' }}
                    title="These addresses are in no stored leaderboard batch right now — nothing honest to render for them.">
                {walletFilter.length - shown.length} linked wallet{walletFilter.length - shown.length === 1 ? '' : 's'} not in any stored batch
              </span>
            )}
            <button type="button"
              className="ml-auto text-[12px] font-semibold px-3 py-1 rounded-[8px]"
              style={{ background: 'var(--accent)', color: '#fff' }}
              onClick={() => navigate('/copy/discover', { replace: true })}>
              Show all traders
            </button>
          </div>
        )}
        {/* Row 1 */}
        <div className="flex items-center gap-3 flex-wrap">
          {/* Exchange segmented — rendered from /api/exchanges */}
          <div className="flex items-center gap-1 p-0.5 bg-bg-secondary rounded-lg shrink-0">
            {exchanges.map((x) => (
              <button
                key={x.id}
                onClick={() => setExchange(x.id)}
                className={clsx(
                  'text-xs font-semibold px-3 py-1.5 rounded-md transition-colors',
                  exchange === x.id ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary',
                )}
              >
                {x.label}
              </button>
            ))}
          </div>
          {/* Search (client-side over the loaded board — name or address) */}
          <div className="relative flex-1 min-w-[180px]">
            <svg className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24" style={{ color: 'var(--faint)' }}>
              <circle cx="11" cy="11" r="7" strokeWidth={2} /><path strokeWidth={2} strokeLinecap="round" d="M21 21l-4-4" />
            </svg>
            <input
              type="search" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search trader or address"
              className="w-full bg-bg-secondary border border-text-secondary/15 rounded-lg pl-9 pr-3 py-1.5 text-xs text-text-primary outline-none focus:border-accent"
            />
          </div>
          {/* View toggle (List default / Card) */}
          <div className="flex items-center gap-0.5 p-0.5 rounded-[11px] shrink-0" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
            {(['list', 'card'] as ViewMode[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                title={v === 'list' ? 'List view' : 'Card view'}
                className={clsx('px-2.5 py-1.5 rounded-[9px] transition-colors', view === v ? 'text-white' : 'text-text-secondary hover:text-text-primary')}
                style={view === v ? { background: 'var(--accent)' } : undefined}
              >
                {v === 'list' ? (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeWidth={2} d="M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z" /></svg>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Row 2: labeled groups */}
        <div className="flex items-stretch gap-4 flex-wrap mt-3">
          {/* Sort by — single-select pills */}
          <div>
            <div className="text-[11px] font-medium mb-1.5" style={{ color: 'var(--faint)' }}>Sort by</div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {SORTS.map((s) => (
                <button
                  key={s.key}
                  title={s.title}
                  onClick={() => setSort(s.key)}
                  className={clsx(
                    'text-xs font-medium px-3 py-1.5 rounded-full border transition-colors',
                    sort === s.key
                      ? 'bg-accent text-white border-transparent'
                      : 'bg-bg-secondary text-text-secondary border-text-secondary/15 hover:text-text-primary',
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div className="w-px self-stretch hidden lg:block" style={{ background: 'var(--border)' }} />
          {/* Window — boxed segmented control (visually distinct from pills) */}
          <div>
            <div className="text-[11px] font-medium mb-1.5" style={{ color: 'var(--faint)' }}>Window</div>
            <div className="flex items-center gap-1 p-0.5 bg-bg-secondary rounded-lg w-fit">
              {periods.map((pk) => (
                <button
                  key={pk}
                  onClick={() => setPeriod(pk)}
                  className={clsx(
                    'text-xs font-medium px-3 py-1.5 rounded-md transition-colors',
                    period === pk ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary',
                  )}
                >
                  {WINDOW_LABELS[pk] ?? pk}
                </button>
              ))}
            </div>
          </div>
          <div className="w-px self-stretch hidden lg:block" style={{ background: 'var(--border)' }} />
          {/* Filters — multi-select check chips (on/off, not a sort) */}
          <div>
            <div className="text-[11px] font-medium mb-1.5" style={{ color: 'var(--faint)' }}>Filters</div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {exchange === 'hl' && (
                <button
                  onClick={() => setActiveProfitable((v) => !v)}
                  title="active ≥5 of last 7 days AND profitable this week AND (win rate ≥50% or not yet measurable) — snapshot + fill-history derived"
                  className={clsx(
                    'text-xs font-medium px-3 py-1.5 rounded-full border transition-colors',
                    activeProfitable ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15 hover:text-text-primary',
                  )}
                >
                  {activeProfitable && <span className="mr-1">✓</span>}Active + profitable
                </button>
              )}
              {exchInfo?.has_live_positions !== false && (
                <button
                  onClick={() => setOpenOnly((v) => !v)}
                  className={clsx(
                    'text-xs font-medium px-3 py-1.5 rounded-full border transition-colors',
                    openOnly ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary border-text-secondary/15 hover:text-text-primary',
                  )}
                >
                  {openOnly && <span className="mr-1">✓</span>}Open trades only
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Count line */}
      {!loading && (
        <div className="rd-mono text-[12px] mb-3" style={{ color: 'var(--faint)' }}>
          {shown.length} trader{shown.length === 1 ? '' : 's'} · {view} view
        </div>
      )}

      {error && (
        <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger mb-4">{error}</div>
      )}

      {loading && traders.length === 0 ? (
        view === 'list' ? (
          /* list-shaped skeleton: rank chip, trader, number columns, buttons */
          <div className="flex flex-col gap-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="grid items-center gap-3 px-4 py-3 rounded-[14px] animate-pulse"
                style={{ gridTemplateColumns: LIST_GRID, background: 'var(--surface)', border: '1px solid var(--border)' }}>
                <div className="w-[34px] h-[34px] rounded-[9px]" style={{ background: 'var(--surface-2)' }} />
                <div className="h-4 rounded w-3/4" style={{ background: 'var(--surface-2)' }} />
                <div className="h-4 rounded w-16" style={{ background: 'var(--surface-2)' }} />
                <div className="h-4 rounded w-12" style={{ background: 'var(--surface-2)' }} />
                <div className="h-4 rounded w-16" style={{ background: 'var(--surface-2)' }} />
                <div className="h-[34px] rounded-[8px] w-[62px]" style={{ background: 'var(--surface-2)' }} />
                <div className="h-8 rounded-[8px]" style={{ background: 'var(--surface-2)' }} />
                <div className="h-8 rounded-[8px] w-32 justify-self-end" style={{ background: 'var(--surface-2)' }} />
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-48 rounded-xl bg-bg-card border border-text-secondary/10 animate-pulse" />
            ))}
          </div>
        )
      ) : shown.length === 0 ? (
        <div className="text-center text-xs text-text-secondary/60 py-16">
          {openOnly ? 'No traders with open trades right now.' : 'No traders found.'}
        </div>
      ) : view === 'list' ? (
        <div className="rd-sans">
          {/* List header */}
          <div className="grid items-center gap-3 px-4 pb-2 text-[10px] font-bold uppercase tracking-wide" style={{ gridTemplateColumns: LIST_GRID, color: 'var(--faint)' }}>
            <span>Rank</span><span>Trader</span><span>PnL</span><span>ROI</span><span>Volume</span><span>Open</span><span>Equity {period === 'day' ? '24h' : 'all'}</span><span />
          </div>
          <div className="flex flex-col gap-2">
            {shown.map((t) => (
              <TraderListRow
                key={t.wallet_address}
                trader={t}
                series={equity[t.wallet_address.toLowerCase()]}
                isWatched={watchlist.isWatched(t.wallet_address, exchange)}
                onWatchToggle={onWatch}
                onStartCopy={(tr) => setCopyTarget(tr)}
                onProfile={onProfile}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {shown.map((t) => (
            <TraderCard
              key={t.wallet_address}
              trader={t}
              series={equity[t.wallet_address.toLowerCase()]}
              isWatched={watchlist.isWatched(t.wallet_address, exchange)}
              onWatchToggle={onWatch}
              onStartCopy={(tr) => setCopyTarget(tr)}
            />
          ))}
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

      {profileWallet && (
        <TraderProfileModal
          wallet={profileWallet}
          timeframe={equityTf}
          exchange={exchange}
          isWatched={watchlist.isWatched(profileWallet, exchange)}
          onWatchToggle={onWatch}
          onCreateSub={subs.create}
          onClose={() => setProfileWallet(null)}
        />
      )}
    </CopyLayout>
  );
}

export default function DiscoverPage() {
  const isMobile = useIsMobile();
  if (isMobile) return <MobileCopy initialTab="discover" />;
  return <DesktopDiscover />;
}
