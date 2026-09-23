import { useState, useMemo, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { clsx } from 'clsx';
import './mobile.css';
import { useAuth } from '@/hooks/useAuth';
import { useTheme } from '@/hooks/useTheme';
import { useTraders, useWatchlist, useSubscriptions, useCopyPortfolio } from '@/hooks/useCopyV1';
import { COPY_LIVE_ENABLED } from '@/config/constants';
import { getEquityBatch, getActiveSummaries, getExchanges, type ExchangeInfo, type TraderListItem, type EquityTimeframe, type ActivePositionSummary } from '@/lib/copyApi';
import { activityBadges } from '@/components/copy/TraderListRow';
import { formatCompact, formatPercent, shortenAddress } from '@/lib/formatters';
import Sparkline from '@/components/copy/Sparkline';
import MobileTraderProfile from '@/components/copy/mobile/MobileTraderProfile';
import MobileCopySheet from '@/components/copy/mobile/MobileCopySheet';

export type MobileCopyTab = 'discover' | 'watchlist' | 'dashboard';

interface Props {
  initialTab?: MobileCopyTab;
}

const SUBTABS: { key: MobileCopyTab; label: string }[] = [
  { key: 'discover', label: 'Discover' },
  { key: 'watchlist', label: 'Watchlist' },
  { key: 'dashboard', label: 'Dashboard' },
];

const THEME_BG = { dark: '#09090f', light: '#faf9ff' };

// ---- Discover filter state (parity with desktop; per-exchange for the session) ----
export interface DiscoverFilters {
  sort: string;
  period: string;
  openOnly: boolean;
  activeProfitable: boolean;
}
export const DEFAULT_FILTERS: DiscoverFilters = {
  sort: 'pnl', period: 'all', openOnly: false, activeProfitable: false,
};

// Same availability rules as desktop (Discover.tsx sortsFor): Consistent and
// Win rate are HL-only (underivable on Perpl snapshots — hidden, never dead).
export const mobileSortsFor = (exchange: string): { key: string; label: string }[] => [
  { key: 'pnl', label: 'Top PnL' },
  { key: 'vol', label: 'Volume' },
  { key: 'active', label: 'Most active' },
  ...(exchange === 'hl' ? [
    { key: 'consistent', label: 'Consistent' },
    { key: 'win_rate', label: 'Win rate' },
  ] : []),
  { key: 'recent_activity', label: 'Recently active' },
];
export const MOBILE_WINDOW_LABELS: Record<string, string> = { all: 'All', day: '24h', week: '1W', month: '1M' };

const DEFAULT_EXCHANGES: ExchangeInfo[] = [
  { id: 'perpl', label: 'Perpl', periods: ['all', 'day'], has_live_positions: true, copy_execution: true },
];

function rankCls(r: number): string {
  return r === 1 ? 'g1' : r === 2 ? 'g2' : r === 3 ? 'g3' : '';
}

// ---- one trader card (real leaderboard row) ----
function TraderCardM({
  trader, series, isWatched, onWatch, onProfile, onCopy,
}: {
  trader: TraderListItem;
  series?: number[];
  isWatched: boolean;
  onWatch: (w: string) => void;
  onProfile: (w: string) => void;
  onCopy: (t: TraderListItem) => void;
}) {
  const open = trader.active_positions_count;
  const pnlPos = trader.pnl_total >= 0;
  const roiPos = trader.roi >= 0;
  const isHl = (trader.exchange ?? 'perpl') === 'hl';
  // Same thresholds as the desktop row badges (TraderListRow.activityBadges);
  // rendered inline in the header line per the mockup ("7/7d · ~14/day").
  const badgeLine = activityBadges(trader).map((b) => b.text.replace(/^\S+\s/, '')).join(' · ');
  return (
    <div className="mc-tcard mc-enter" role="button" tabIndex={0} onClick={() => onProfile(trader.wallet_address)}>
      <div className="mc-tc-top">
        <div className={clsx('mc-rank', rankCls(trader.rank))}>{trader.rank}</div>
        <div className="mc-tc-id">
          <div className="mc-tc-addr">
            {trader.display_name || shortenAddress(trader.wallet_address)}
            {isHl && <span className="mc-hl-tag">HL</span>}
          </div>
          <div className="mc-tc-sub">
            {isHl ? 'on Hyperliquid' : 'on Monad'}{' · '}
            {trader.active_positions_pending
              ? <span className="mc-checking">checking…</span>
              : open != null
                ? <><b>{open}</b> open</>
                : <span className="mc-checking">—</span>}
          </div>
        </div>
        {badgeLine && <div className="mc-tc-badges">{badgeLine}</div>}
        <button
          type="button"
          aria-label={isWatched ? 'Unwatch' : 'Watch'}
          className={clsx('mc-wstar', isWatched && 'on')}
          onClick={(e) => { e.stopPropagation(); onWatch(trader.wallet_address); }}
        >
          <svg viewBox="0 0 24 24" fill={isWatched ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.9">
            <polygon points="12 2.5 14.85 8.6 21.5 9.3 16.5 13.9 17.9 20.5 12 17 6.1 20.5 7.5 13.9 2.5 9.3 9.15 8.6" />
          </svg>
        </button>
      </div>
      <div className="mc-tc-stats">
        <div className="mc-tc-stat"><div className="k">PnL</div><div className={clsx('v', pnlPos ? 'mc-v-pos' : 'mc-v-neg')}>{formatCompact(trader.pnl_total)}</div></div>
        <div className="mc-tc-stat"><div className="k">ROI</div><div className={clsx('v', roiPos ? 'mc-v-pos' : 'mc-v-neg')}>{formatPercent(trader.roi)}</div></div>
        <div className="mc-tc-stat"><div className="k">Volume</div><div className="v">{formatCompact(trader.volume)}</div></div>
      </div>
      <div className="mc-tc-spark">
        <Sparkline points={series} width={320} height={40} animate={false} />
      </div>
      <div className="mc-tc-actions">
        <button type="button" className="mc-btn" onClick={(e) => { e.stopPropagation(); onProfile(trader.wallet_address); }}>Profile</button>
        <button type="button" className="mc-btn mc-btn-copy" onClick={(e) => { e.stopPropagation(); onCopy(trader); }}>Set up copy</button>
      </div>
    </div>
  );
}

export default function MobileCopy({ initialTab = 'discover' }: Props) {
  const { isAuthenticated } = useAuth();
  const { theme } = useTheme();
  const [tab, setTab] = useState<MobileCopyTab>(initialTab);

  // Discover filters (drive the real leaderboard query) — full desktop parity:
  // exchange from /api/exchanges, per-exchange filter state for the session.
  const [exchanges, setExchanges] = useState<ExchangeInfo[]>(DEFAULT_EXCHANGES);
  const [exchange, setExchange] = useState('perpl');
  useEffect(() => {
    getExchanges().then((xs) => { if (xs?.length) setExchanges(xs); }).catch(() => {});
  }, []);
  const [filtersByExchange, setFiltersByExchange] = useState<Record<string, DiscoverFilters>>({});
  const filters = filtersByExchange[exchange] ?? DEFAULT_FILTERS;
  const setFilters = useCallback((f: DiscoverFilters) => {
    setFiltersByExchange((prev) => ({ ...prev, [exchange]: f }));
  }, [exchange]);
  const { sort, period, openOnly, activeProfitable } = filters;

  // Same limits as desktop (HL serves the stored top-100 batch; Perpl live passthrough)
  const { traders, loading, error } = useTraders({ sort, period, limit: exchange === 'hl' ? 50 : 24, exchange });
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);

  const [profileWallet, setProfileWallet] = useState<string | null>(null);
  const [copyTarget, setCopyTarget] = useState<{ wallet: string; name: string | null } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = useCallback((m: string) => {
    setToast(m);
    window.setTimeout(() => setToast(null), 2600);
  }, []);

  // Keep the browser/PWA status bar colour in sync with the active theme.
  useEffect(() => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', THEME_BG[theme]);
  }, [theme]);

  // Real equity sparklines (leaderboard_snapshots) for the visible wallet set + timeframe.
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

  // Same client-side filter predicates as desktop Discover
  const applyFilters = useCallback((rows: TraderListItem[], f: DiscoverFilters, exch: string) => {
    let out = f.openOnly ? rows.filter((t) => t.has_active_positions || t.active_positions_pending) : rows;
    if (f.activeProfitable && exch === 'hl') {
      out = out.filter((t) =>
        (t.activity?.active_days_7d ?? 0) >= 5
        && ((t.activity?.consistency_flags ?? 0) & 2) !== 0
        && (t.fill_stats?.win_rate_7d == null || t.fill_stats.win_rate_7d >= 0.5));
    }
    return out;
  }, []);
  const shown = useMemo(() => applyFilters(traders, filters, exchange), [applyFilters, traders, filters, exchange]);

  const onWatch = useCallback(async (w: string) => {
    if (!isAuthenticated) { showToast('Connect your wallet to watch traders'); return; }
    if (watchlist.isWatched(w, exchange)) await watchlist.unwatch(w, exchange);
    else await watchlist.watch(w, exchange);
  }, [isAuthenticated, watchlist, showToast, exchange]);

  const onCopy = useCallback((t: { wallet_address: string; display_name: string | null }) => {
    if (!isAuthenticated) { showToast('Connect your wallet to set up copy'); return; }
    setCopyTarget({ wallet: t.wallet_address, name: t.display_name });
  }, [isAuthenticated, showToast]);

  const switchTab = (t: MobileCopyTab) => {
    setTab(t);
    // reset the copy scroll region (the app Layout <main>) to top
    const main = document.querySelector('main');
    if (main) main.scrollTop = 0;
  };

  const liveOn = COPY_LIVE_ENABLED;

  return (
    <div className="mc-root">
      <svg className="mc-wm" viewBox="0 0 100 100" fill="currentColor" aria-hidden>
        <defs>
          <mask id="mcwm">
            <rect x="20" y="20" width="60" height="60" rx="20" fill="#fff" transform="rotate(45 50 50)" />
            <rect x="34" y="34" width="32" height="32" rx="11" fill="#000" transform="rotate(45 50 50)" />
          </mask>
        </defs>
        <rect width="100" height="100" mask="url(#mcwm)" />
      </svg>

      <div className="mc-body">
        {/* screen head */}
        <div className="mc-shead">
          <div className="mc-title-row">
            <h1>Copy Trading</h1>
            <span className={clsx('mc-live', liveOn && 'on')}>
              <span className="mc-pip" />{liveOn ? 'LIVE — ON' : 'LIVE — OFF'}
            </span>
          </div>
          <div className="mc-subtitle">Discover top traders, watch them, and copy their trades — you confirm every order.</div>
        </div>

        {/* warning banner (reflects real live state) */}
        <div className="mc-warn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0M12 9v4M12 17h.01" /></svg>
          <div>
            <b>Live copy places real orders</b>{' '}
            <span>{liveOn ? '— you confirm each order before it is placed.' : '— currently OFF, so nothing is placed yet.'}</span>
          </div>
        </div>

        {/* segmented sub-tabs */}
        <div className="mc-subtabs">
          {SUBTABS.map((t) => (
            <button key={t.key} className={clsx('mc-subtab', tab === t.key && 'on')} onClick={() => switchTab(t.key)}>{t.label}</button>
          ))}
        </div>

        {tab === 'discover' && (
          <DiscoverBody
            traders={shown} rawTraders={traders} equity={equity} loading={loading} error={error}
            exchange={exchange} exchanges={exchanges} setExchange={setExchange}
            filters={filters} setFilters={setFilters} applyFilters={applyFilters}
            isWatched={(w) => watchlist.isWatched(w, exchange)} onWatch={onWatch}
            onProfile={setProfileWallet} onCopy={onCopy}
          />
        )}

        {tab === 'watchlist' && (
          <WatchlistBody
            authed={isAuthenticated} watchlist={watchlist} traders={traders} equity={equity}
            onProfile={setProfileWallet} onCopy={onCopy} onWatch={onWatch}
          />
        )}

        {tab === 'dashboard' && (
          <DashboardBody authed={isAuthenticated} subs={subs} goDiscover={() => switchTab('discover')} />
        )}
      </div>

      {profileWallet && (
        <MobileTraderProfile
          wallet={profileWallet}
          timeframe={equityTf}
          exchange={exchange}
          isWatched={watchlist.isWatched(profileWallet, exchange)}
          onWatchToggle={onWatch}
          onSetupCopy={(w, name) => { setProfileWallet(null); onCopy({ wallet_address: w, display_name: name }); }}
          onClose={() => setProfileWallet(null)}
        />
      )}

      {copyTarget && (
        <MobileCopySheet
          traderWallet={copyTarget.wallet}
          traderName={copyTarget.name}
          onSubmit={subs.create}
          onSaved={() => { setCopyTarget(null); showToast(`Copy saved for ${copyTarget.name || shortenAddress(copyTarget.wallet)}`); }}
          onClose={() => setCopyTarget(null)}
        />
      )}

      {createPortal(
        <div className={clsx('mc-toast', toast && 'show')}>
          <span className="d" />{toast}
        </div>,
        document.body,
      )}
    </div>
  );
}

// ------------------------------ Discover ------------------------------
function DiscoverBody({
  traders, rawTraders, equity, loading, error,
  exchange, exchanges, setExchange, filters, setFilters, applyFilters,
  isWatched, onWatch, onProfile, onCopy,
}: {
  traders: TraderListItem[]; rawTraders: TraderListItem[]; equity: Record<string, number[]>;
  loading: boolean; error: string | null;
  exchange: string; exchanges: ExchangeInfo[]; setExchange: (id: string) => void;
  filters: DiscoverFilters; setFilters: (f: DiscoverFilters) => void;
  applyFilters: (rows: TraderListItem[], f: DiscoverFilters, exch: string) => TraderListItem[];
  isWatched: (w: string) => boolean; onWatch: (w: string) => void;
  onProfile: (w: string) => void; onCopy: (t: TraderListItem) => void;
}) {
  // ONE bottom sheet for everything; the tapped chip decides the section it
  // opens scrolled to. Exchange applies INSTANTLY (desktop-tab behavior);
  // sort/window/filters are draft state applied on the primary button.
  const [sheetSection, setSheetSection] = useState<null | 'exchange' | 'sort' | 'window' | 'filters'>(null);
  const sorts = mobileSortsFor(exchange);
  const sortLabel = sorts.find((s) => s.key === filters.sort)?.label ?? 'Top PnL';
  const exchInfo = exchanges.find((x) => x.id === exchange) ?? exchanges[0];
  const activeFilterCount = (filters.openOnly ? 1 : 0) + (filters.activeProfitable ? 1 : 0);

  return (
    <>
      {/* Compact state bar: current values always visible, scrolls horizontally */}
      <div className="mc-pills mc-chipbar">
        <button className="mc-pill mc-chip on" onClick={() => setSheetSection('exchange')}>
          {exchInfo?.label ?? exchange} <span className="mc-caret">▾</span>
        </button>
        <button className="mc-pill mc-chip" onClick={() => setSheetSection('sort')}>
          {sortLabel} <span className="mc-caret">▾</span>
        </button>
        <button className="mc-pill mc-chip" onClick={() => setSheetSection('window')}>
          {MOBILE_WINDOW_LABELS[filters.period] ?? filters.period} <span className="mc-caret">▾</span>
        </button>
        <button className={clsx('mc-pill mc-chip mc-fchip', activeFilterCount > 0 && 'on')}
          aria-label="Filters" onClick={() => setSheetSection('filters')}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="13" height="13">
            <path d="M3 5h18M7 12h10M10 19h4" strokeLinecap="round" />
          </svg>
          {activeFilterCount > 0 && <span className="mc-fcount">{activeFilterCount}</span>}
        </button>
      </div>

      {!loading && (
        <div className="mc-count">{traders.length} trader{traders.length === 1 ? '' : 's'} · {sortLabel}</div>
      )}

      {error && <div className="mc-err-line">{error}</div>}

      {loading && traders.length === 0 ? (
        <div className="mc-tcards">{Array.from({ length: 4 }).map((_, i) => <div key={i} className="mc-skel" />)}</div>
      ) : traders.length === 0 ? (
        <div className="mc-empty">
          <div className="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></svg></div>
          <h3>{filters.openOnly || filters.activeProfitable ? 'No matching traders' : 'No traders found'}</h3>
          <p>{filters.openOnly || filters.activeProfitable ? 'No traders match the current filters right now.' : 'Check back shortly.'}</p>
        </div>
      ) : (
        <div className="mc-tcards">
          {traders.map((t) => (
            <TraderCardM
              key={t.wallet_address}
              trader={t}
              series={equity[t.wallet_address.toLowerCase()]}
              isWatched={isWatched(t.wallet_address)}
              onWatch={onWatch}
              onProfile={onProfile}
              onCopy={onCopy}
            />
          ))}
        </div>
      )}

      {sheetSection && (
        <FilterSheet
          initialSection={sheetSection}
          exchange={exchange} exchanges={exchanges}
          filters={filters} rawTraders={rawTraders} applyFilters={applyFilters}
          onInstantExchange={(id) => { setExchange(id); setSheetSection(null); }}
          onApply={(f) => { setFilters(f); setSheetSection(null); }}
          onClose={() => setSheetSection(null)}
        />
      )}
    </>
  );
}

// ------------------------------ Sort and filter sheet ------------------------------
// Plain CSS transform/transition, safe-area aware, PWA-standalone safe.
// No gesture lib exists in the project, so swipe-down is intentionally omitted
// (backdrop tap + the drag-handle row act as close targets).
function FilterSheet({
  initialSection, exchange, exchanges, filters, rawTraders, applyFilters,
  onInstantExchange, onApply, onClose,
}: {
  initialSection: 'exchange' | 'sort' | 'window' | 'filters';
  exchange: string; exchanges: ExchangeInfo[];
  filters: DiscoverFilters; rawTraders: TraderListItem[];
  applyFilters: (rows: TraderListItem[], f: DiscoverFilters, exch: string) => TraderListItem[];
  onInstantExchange: (id: string) => void;
  onApply: (f: DiscoverFilters) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<DiscoverFilters>(filters);
  const [visible, setVisible] = useState(false);
  const sortRef = { exchange: 'mc-sec-exchange', sort: 'mc-sec-sort', window: 'mc-sec-window', filters: 'mc-sec-filters' }[initialSection];
  const exchInfo = exchanges.find((x) => x.id === exchange) ?? exchanges[0];
  const periods = exchInfo?.periods ?? ['all', 'day'];
  const sorts = mobileSortsFor(exchange);

  // slide in on mount; open scrolled to the launching chip's section
  useEffect(() => {
    requestAnimationFrame(() => {
      setVisible(true);
      document.getElementById(sortRef)?.scrollIntoView({ block: 'start' });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const close = () => { setVisible(false); window.setTimeout(onClose, 240); };

  // "Show N traders": N is derivable CLIENT-SIDE only when the draft keeps the
  // fetched dataset (same window — sorting never changes membership). A window
  // change needs a new fetch, so the button honestly says "Apply" instead of
  // inventing a count. (Report: count-button implementation choice.)
  const n = draft.period === filters.period
    ? applyFilters(rawTraders, draft, exchange).length
    : null;

  return createPortal(
    <div className={clsx('mc-sheet-back', visible && 'show')} onClick={close}>
      <div className={clsx('mc-sheet', visible && 'open')} role="dialog" aria-label="Sort and filter"
        onClick={(e) => e.stopPropagation()}>
        <button className="mc-sheet-handle" aria-label="Close" onClick={close}><span /></button>
        <div className="mc-sheet-head">
          <h3>Sort and filter</h3>
          <button className="mc-sheet-reset" onClick={() => setDraft(DEFAULT_FILTERS)}>Reset</button>
        </div>

        {/* Exchange — INSTANT apply (desktop-tab behavior: rows clear + skeleton) */}
        <div id="mc-sec-exchange" className="mc-sheet-sec">
          <div className="mc-sheet-label">Exchange</div>
          <div className="mc-seg">
            {exchanges.map((x) => (
              <button key={x.id} className={clsx(exchange === x.id && 'on')}
                onClick={() => { if (x.id !== exchange) onInstantExchange(x.id); }}>
                {x.label}
              </button>
            ))}
          </div>
        </div>

        <div id="mc-sec-sort" className="mc-sheet-sec">
          <div className="mc-sheet-label">Sort by</div>
          <div className="mc-sheet-pills">
            {sorts.map((s) => (
              <button key={s.key} className={clsx('mc-pill', draft.sort === s.key && 'solid')}
                onClick={() => setDraft({ ...draft, sort: s.key })}>
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div id="mc-sec-window" className="mc-sheet-sec">
          <div className="mc-sheet-label">Window</div>
          <div className="mc-seg">
            {periods.map((pk) => (
              <button key={pk} className={clsx(draft.period === pk && 'on')}
                onClick={() => setDraft({ ...draft, period: pk })}>
                {MOBILE_WINDOW_LABELS[pk] ?? pk}
              </button>
            ))}
          </div>
        </div>

        <div id="mc-sec-filters" className="mc-sheet-sec">
          <div className="mc-sheet-label">Filters</div>
          {exchange === 'hl' && (
            <button className="mc-frow" onClick={() => setDraft({ ...draft, activeProfitable: !draft.activeProfitable })}>
              <span>Active + profitable</span>
              <span className={clsx('mc-switch', draft.activeProfitable && 'on')}><span className="knob" /></span>
            </button>
          )}
          {exchInfo?.has_live_positions !== false && (
            <button className="mc-frow" onClick={() => setDraft({ ...draft, openOnly: !draft.openOnly })}>
              <span>Open trades only</span>
              <span className={clsx('mc-switch', draft.openOnly && 'on')}><span className="knob" /></span>
            </button>
          )}
        </div>

        <button className="mc-sheet-cta" onClick={() => onApply(draft)}>
          {n != null ? `Show ${n} trader${n === 1 ? '' : 's'}` : 'Apply'}
        </button>
      </div>
    </div>,
    document.body,
  );
}

// ------------------------------ Watchlist ------------------------------
function WatchlistBody({
  authed, watchlist, traders, equity, onProfile, onCopy, onWatch,
}: {
  authed: boolean;
  watchlist: ReturnType<typeof useWatchlist>;
  traders: TraderListItem[];
  equity: Record<string, number[]>;
  onProfile: (w: string) => void; onCopy: (t: TraderListItem) => void; onWatch: (w: string) => void;
}) {
  const [summaries, setSummaries] = useState<Record<string, ActivePositionSummary>>({});
  const items = watchlist.items;

  useEffect(() => {
    if (!items.length) return;
    let cancelled = false;
    getActiveSummaries(items.map((i) => i.trader_wallet)).then((s) => { if (!cancelled) setSummaries(s); }).catch(() => {});
    return () => { cancelled = true; };
  }, [items]);

  if (!authed) {
    return (
      <div className="mc-empty">
        <div className="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M12 2 4 6v6c0 5 3.4 7.7 8 10 4.6-2.3 8-5 8-10V6z" /></svg></div>
        <h3>Connect to view watchlist</h3>
        <p>Connect your wallet to see the traders you're watching.</p>
      </div>
    );
  }

  if (watchlist.loading) {
    return <div className="mc-tcards">{Array.from({ length: 3 }).map((_, i) => <div key={i} className="mc-skel" />)}</div>;
  }

  if (!items.length) {
    return (
      <div className="mc-empty">
        <div className="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"><polygon points="12 2 15 9 22 9.3 17 14 18.5 21 12 17.3 5.5 21 7 14 2 9.3 9 9" /></svg></div>
        <h3>Watchlist is empty</h3>
        <p>Tap the star on any trader in Discover and they'll show up here.</p>
      </div>
    );
  }

  const byWallet = new Map(traders.map((t) => [t.wallet_address.toLowerCase(), t]));

  return (
    <>
      <div className="mc-count">{items.length} watched</div>
      <div className="mc-tcards">
        {items.map((w) => {
          const full = byWallet.get(w.trader_wallet.toLowerCase());
          if (full) {
            return (
              <TraderCardM
                key={w.id}
                trader={full}
                series={equity[full.wallet_address.toLowerCase()]}
                isWatched
                onWatch={onWatch}
                onProfile={onProfile}
                onCopy={onCopy}
              />
            );
          }
          // Watched trader not in the current leaderboard page — compact real row
          // (name + real active-position badge). Full stats load on profile open.
          const s = summaries[w.trader_wallet.toLowerCase()];
          const cnt = s?.active_positions_count;
          return (
            <div key={w.id} className="mc-sub">
              <div className="mc-sub-main" onClick={() => onProfile(w.trader_wallet)} style={{ cursor: 'pointer' }}>
                <div className="mc-sub-addr">{w.display_name || shortenAddress(w.trader_wallet)}</div>
                <div className="mc-sub-meta">
                  {cnt != null && cnt > 0
                    ? `${cnt} active · ${s!.active_markets.slice(0, 3).map((m) => m.symbol).join(', ')}`
                    : cnt === 0 ? 'No active trades' : s ? 'Unavailable' : 'Checking…'}
                </div>
              </div>
              <button className="mc-sub-btn" onClick={() => onProfile(w.trader_wallet)}>Profile</button>
              <button type="button" aria-label="Unwatch" className="mc-wstar on" onClick={() => onWatch(w.trader_wallet)}>
                <svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" strokeWidth="1.9"><polygon points="12 2.5 14.85 8.6 21.5 9.3 16.5 13.9 17.9 20.5 12 17 6.1 20.5 7.5 13.9 2.5 9.3 9.15 8.6" /></svg>
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ------------------------------ Dashboard ------------------------------
function DashboardBody({
  authed, subs, goDiscover,
}: {
  authed: boolean;
  subs: ReturnType<typeof useSubscriptions>;
  goDiscover: () => void;
}) {
  const portfolio = useCopyPortfolio();

  if (!authed) {
    return (
      <div className="mc-empty">
        <div className="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18" /></svg></div>
        <h3>Connect to view dashboard</h3>
        <p>Connect your wallet to see your active copies and copied positions.</p>
      </div>
    );
  }

  const active = subs.subscriptions.filter((s) => s.status === 'active');
  const activeCount = active.length;
  const realized = portfolio.summary.realized;
  const openCount = portfolio.open.length;
  const dailyLimit = active.reduce((a, s) => a + (Number(s.max_daily_loss) || 0), 0);

  return (
    <div className="mc-dash">
      <div className="mc-dgrid">
        <div className="mc-dcard"><div className="k">Active copies</div><div className="v">{activeCount}</div><div className="s">{activeCount ? 'Subscriptions' : 'None yet'}</div></div>
        <div className="mc-dcard"><div className="k">Realized PnL</div><div className="v" style={{ color: realized >= 0 ? 'var(--green)' : 'var(--red)' }}>{formatCompact(realized)}</div><div className="s">Closed copy trades</div></div>
        <div className="mc-dcard"><div className="k">Copied pos.</div><div className="v">{openCount}</div><div className="s">Currently open</div></div>
        <div className="mc-dcard"><div className="k">Daily loss limit</div><div className="v">{dailyLimit ? formatCompact(dailyLimit) : '—'}</div><div className="s">Configured cap · used n/a</div></div>
      </div>

      {subs.loading ? (
        <div className="mc-tcards">{Array.from({ length: 2 }).map((_, i) => <div key={i} className="mc-skel" style={{ height: 66 }} />)}</div>
      ) : subs.subscriptions.length === 0 ? (
        <div className="mc-dpanel">
          <h3>No copies set up yet</h3>
          <p>Open a trader in Discover and tap Set up copy to define your risk limits. Active copies show here.</p>
          <button className="mc-btn mc-btn-copy" style={{ padding: '11px 20px' }} onClick={goDiscover}>Browse traders</button>
        </div>
      ) : (
        <div className="mc-tcards">
          {subs.subscriptions.map((s) => (
            <div key={s.id} className="mc-sub">
              <div className="mc-sub-main">
                <div className="mc-sub-addr">{shortenAddress(s.trader_wallet)}</div>
                <div className="mc-sub-meta">Alloc {formatCompact(Number(s.allocation_usd) || 0)} · {s.max_leverage}x max</div>
              </div>
              <span className={clsx('mc-badge', s.status)}>{s.status}</span>
              {s.status === 'active' ? (
                <button className="mc-sub-btn" onClick={() => subs.pause(s.id)}>Pause</button>
              ) : s.status === 'paused' ? (
                <button className="mc-sub-btn" onClick={() => subs.resume(s.id)}>Resume</button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
