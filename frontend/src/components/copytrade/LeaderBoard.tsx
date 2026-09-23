import { useState, useMemo, useCallback, useRef, useEffect, Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useLeaderboard } from '@/hooks/useLeaderboard';
import type { LeaderboardTab } from '@/hooks/useLeaderboard';
import { shortenAddress, displayName, formatUSD, formatPercent, formatCompact, formatPrice } from '@/lib/formatters';
import { getTraderPositions, getMyFollows } from '@/lib/api';
import CopyModal from '@/components/copytrade/CopyModal';
import FollowModal from '@/components/copytrade/FollowModal';
import LeaderCard from '@/components/copytrade/LeaderCard';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useCopyTrading } from '@/hooks/useCopyTrading';
import { useAuth } from '@/hooks/useAuth';
import type { Leader } from '@/types/copytrade';

interface SortableHeaderProps {
  field: string;
  label: string;
  sortBy: string;
  onSort: (field: string) => void;
}

function SortableHeader({ field, label, sortBy, onSort }: SortableHeaderProps) {
  const isActive =
    sortBy === field ||
    (sortBy === 'pnl' && field === 'pnl_total') ||
    (sortBy === 'vol' && field === 'volume');
  return (
    <th
      className="px-3 py-3 text-left text-[11px] font-semibold text-text-secondary uppercase tracking-wider cursor-pointer hover:text-accent transition-colors select-none"
      onClick={() => onSort(field)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {isActive && <span className="text-accent">{'\u25BC'}</span>}
      </span>
    </th>
  );
}

function PositionDetail({ walletAddress, isFollowed, onFollow }: { walletAddress: string; isFollowed: boolean; onFollow: () => void }) {
  const [copyPos, setCopyPos] = useState<any>(null);
  const { isAuthenticated } = useAuth();

  const { data, isLoading, error } = useQuery({
    queryKey: ['trader-positions', walletAddress],
    queryFn: () => getTraderPositions(walletAddress),
    staleTime: 15_000,
    enabled: isAuthenticated && isFollowed,
  });

  if (!isAuthenticated) {
    return (
      <div className="px-6 py-8 bg-gradient-to-b from-accent/5 to-transparent text-center">
        <p className="text-sm text-text-secondary mb-3">Connect your wallet to see trader details</p>
      </div>
    );
  }

  if (!isFollowed) {
    return (
      <div className="px-6 py-8 bg-gradient-to-b from-accent/5 to-transparent text-center">
        <p className="text-sm text-text-secondary mb-3">Follow this trader to see their live positions and trades</p>
        <button onClick={onFollow} className="px-5 py-2 rounded-lg text-xs font-bold bg-accent/10 text-accent border border-accent/20 hover:bg-accent hover:text-white transition-all">
          Follow Trader
        </button>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="px-6 py-6 bg-gradient-to-b from-accent/5 to-transparent">
        <LoadingSpinner size="sm" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="px-6 py-4 bg-gradient-to-b from-accent/5 to-transparent text-xs text-text-secondary">
        No on-chain data available
      </div>
    );
  }

  const hasPositions = data.positions?.length > 0;
  const hasOrders = data.orders?.length > 0;

  return (
    <div className="px-6 py-5 bg-gradient-to-b from-accent/5 to-transparent border-t border-accent/10 space-y-4 animate-fadeInUp">
      {/* Account bar */}
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <span className="text-text-secondary">
          Account <span className="text-accent font-mono font-bold">#{data.account_id}</span>
        </span>
        <span className="text-text-secondary">
          Balance: <span className="text-text-primary font-medium">{formatUSD(data.balance)}</span>
        </span>
        <span className="text-text-secondary">
          Margin: <span className="text-text-primary font-medium">{formatUSD(data.margin_used)}</span>
        </span>
        <a
          href="/settings"
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-bg-secondary hover:bg-accent/10 transition-colors"
          title="Manage Telegram notifications in Settings"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          <span className="text-text-secondary">Telegram Alerts</span>
        </a>
      </div>

      {/* Open Positions */}
      {hasPositions && (
        <div className="space-y-2 stagger-children">
          <div className="text-[10px] text-accent font-semibold uppercase tracking-widest">Open Positions</div>
          {data.positions.map((pos: any) => {
            const priceDec = pos.symbol === 'MON' ? 6 : 2;
            return (
              <div key={`pos-${pos.market_id}`} className="p-3 bg-bg-card/80 rounded-lg text-xs border border-text-secondary/5 hover:border-accent/20 transition-all duration-300">
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  <span className="font-bold text-text-primary text-sm">{pos.symbol}</span>
                  <span className={clsx('font-semibold uppercase px-1.5 py-0.5 rounded text-[10px]', pos.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>{pos.side}</span>
                  <span className="text-text-secondary">{pos.leverage}x</span>
                  <div className="ml-auto flex flex-col items-end gap-0.5">
                    <button
                      onClick={(e) => { e.stopPropagation(); setCopyPos(pos); }}
                      className="px-4 py-1.5 rounded-lg text-[11px] font-bold bg-accent text-white hover:bg-accent-dark hover:shadow-lg hover:shadow-accent/30 active:scale-95 transition-all duration-200"
                    >
                      Copy Trade
                    </button>
                    <span className="text-[9px] text-text-secondary/60 leading-tight">Mirror this position on your account</span>
                  </div>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
                  <div><div className="text-text-secondary">Size</div><div className="text-text-primary font-medium truncate">{pos.size.toFixed(4)}</div></div>
                  <div><div className="text-text-secondary">Entry</div><div className="text-text-primary font-medium truncate">${formatPrice(pos.entry_price, priceDec)}</div></div>
                  <div><div className="text-text-secondary">Mark</div><div className="text-text-primary font-medium truncate">${formatPrice(pos.mark_price, priceDec)}</div></div>
                  <div><div className="text-text-secondary">PnL</div><div className={clsx('font-bold truncate', pos.pnl >= 0 ? 'text-success' : 'text-danger')}>{pos.pnl >= 0 ? '+' : ''}{formatUSD(pos.pnl)}</div></div>
                  <div><div className="text-text-secondary">Notional</div><div className="text-text-primary font-medium truncate">{formatCompact(pos.notional)}</div></div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pending Orders */}
      {hasOrders && (
        <div className="space-y-2">
          <div className="text-[10px] text-text-secondary font-semibold uppercase tracking-widest">Pending Orders</div>
          {data.orders.map((ord: any, i: number) => (
            <div key={`ord-${ord.market_id}-${i}`} className="flex flex-wrap items-center gap-2 sm:gap-4 p-3 bg-bg-card/80 rounded-lg text-xs border border-text-secondary/5">
              <span className="font-bold text-text-primary text-sm">{ord.symbol}</span>
              <span className={clsx('font-semibold uppercase px-1.5 py-0.5 rounded text-[10px]', ord.side === 'buy' ? 'text-success bg-success/10' : ord.side === 'sell' ? 'text-danger bg-danger/10' : 'text-warning bg-warning/10')}>{ord.side}</span>
              <span className="text-text-secondary capitalize">{ord.order_type.replace('_', ' ')}</span>
              <span className="text-warning font-medium ml-auto">{formatUSD(ord.margin_locked)}</span>
            </div>
          ))}
        </div>
      )}

      {!hasPositions && !hasOrders && (
        <div className="text-xs text-text-secondary">No open positions or pending orders</div>
      )}

      {copyPos && (
        <CopyModal position={copyPos} copiedFrom={walletAddress} onClose={() => setCopyPos(null)} />
      )}
    </div>
  );
}

const TABS: { key: LeaderboardTab; label: string }[] = [
  { key: 'all', label: 'All Time' },
  { key: 'day', label: '24h' },
  { key: 'active', label: 'Active' },
];

export default function LeaderBoard() {
  const {
    leaders, isLoading, sortBy, tab, switchTab, toggleSort, page, setPage, hasMore,
  } = useLeaderboard();
  const [followLeader, setFollowLeader] = useState<Leader | null>(null);
  const [expandedWallet, setExpandedWallet] = useState<string | null>(null);
  const [unfollowConfirm, setUnfollowConfirm] = useState<string | null>(null);
  const { isAuthenticated } = useAuth();
  const { follows, unfollow, isUnfollowing } = useCopyTrading();

  // Direct query as fallback — ensures follows load even if hook auth state is delayed
  const { data: followsDirect } = useQuery({
    queryKey: ['my-follows'],
    queryFn: () => getMyFollows(),
    enabled: isAuthenticated,
    staleTime: 5000,
  });

  const activeFollows = follows?.length ? follows : followsDirect || [];
  const followedWallets = useMemo(
    () => new Set(activeFollows.map((f: any) => (f.leader_wallet || '').toLowerCase())),
    [activeFollows],
  );

  // Search & Filter state
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [showFilters, setShowFilters] = useState(false);
  const [minPnl, setMinPnl] = useState<number | ''>('');
  const [minRoi, setMinRoi] = useState<number | ''>('');
  const [minVolume, setMinVolume] = useState<number | ''>('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounced search
  const handleSearchChange = useCallback((value: string) => {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setSearchQuery(value.toLowerCase().trim()), 300);
  }, []);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  // Filter leaders client-side
  const filteredLeaders = useMemo(() => {
    return leaders.filter((leader) => {
      if (searchQuery && !leader.wallet_address.toLowerCase().includes(searchQuery)) return false;
      if (minPnl !== '' && leader.pnl_total < minPnl) return false;
      if (minRoi !== '' && leader.roi < minRoi) return false;
      if (minVolume !== '' && leader.volume < minVolume) return false;
      return true;
    });
  }, [leaders, searchQuery, minPnl, minRoi, minVolume]);

  const hasActiveFilters = searchQuery || minPnl !== '' || minRoi !== '' || minVolume !== '';

  const toggleExpand = (wallet: string) => {
    setExpandedWallet((prev) => (prev === wallet ? null : wallet));
  };

  return (
    <>
      {/* Period Tabs */}
      <div className="flex items-center gap-2 mb-4">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => switchTab(t.key)}
            className={clsx(
              'px-4 py-2 min-h-[44px] rounded-lg text-xs font-semibold transition-all duration-300',
              tab === t.key
                ? 'bg-accent/15 text-accent border border-accent/30 shadow-sm shadow-accent/10'
                : 'text-text-secondary hover:text-text-primary bg-bg-secondary/50 border border-transparent hover:border-text-secondary/10',
            )}
          >
            {t.label}
          </button>
        ))}
        <span className="text-[10px] text-text-secondary/60 ml-2 hidden sm:inline">
          Live from Perpl
        </span>
      </div>

      {/* Search & Filter Bar */}
      <div className="mb-4 space-y-2">
        <div className="flex items-center gap-2">
          {/* Search input */}
          <div className="relative flex-1">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-secondary/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              value={searchInput}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder="Search by wallet address..."
              className="w-full pl-9 pr-3 py-2.5 min-h-[44px] bg-bg-secondary border border-text-secondary/10 rounded-lg text-xs text-text-primary placeholder-text-secondary/50 focus:outline-none focus:border-accent/30 transition-colors"
            />
          </div>
          {/* Filter toggle */}
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={clsx(
              'p-2.5 min-h-[44px] min-w-[44px] flex items-center justify-center rounded-lg border transition-all',
              showFilters || hasActiveFilters
                ? 'bg-accent/10 border-accent/30 text-accent'
                : 'bg-bg-secondary border-text-secondary/10 text-text-secondary hover:text-text-primary',
            )}
            title="Filters"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
            </svg>
          </button>
        </div>

        {/* Filter row (expandable) */}
        {showFilters && (
          <div className="flex flex-wrap items-center gap-3 p-3 bg-bg-secondary/50 rounded-lg border border-text-secondary/10">
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-text-secondary uppercase tracking-wider whitespace-nowrap">Min PnL</label>
              <input
                type="number"
                value={minPnl}
                onChange={(e) => setMinPnl(e.target.value ? Number(e.target.value) : '')}
                placeholder="$0"
                className="w-24 px-2 py-1.5 min-h-[36px] bg-bg-card border border-text-secondary/10 rounded text-xs text-text-primary focus:outline-none focus:border-accent/30"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-text-secondary uppercase tracking-wider whitespace-nowrap">Min ROI</label>
              <input
                type="number"
                value={minRoi}
                onChange={(e) => setMinRoi(e.target.value ? Number(e.target.value) : '')}
                placeholder="0%"
                className="w-24 px-2 py-1.5 min-h-[36px] bg-bg-card border border-text-secondary/10 rounded text-xs text-text-primary focus:outline-none focus:border-accent/30"
              />
            </div>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-text-secondary uppercase tracking-wider whitespace-nowrap">Min Volume</label>
              <input
                type="number"
                value={minVolume}
                onChange={(e) => setMinVolume(e.target.value ? Number(e.target.value) : '')}
                placeholder="$0"
                className="w-24 px-2 py-1.5 min-h-[36px] bg-bg-card border border-text-secondary/10 rounded text-xs text-text-primary focus:outline-none focus:border-accent/30"
              />
            </div>
            {hasActiveFilters && (
              <button
                onClick={() => { setSearchInput(''); setSearchQuery(''); setMinPnl(''); setMinRoi(''); setMinVolume(''); }}
                className="text-[10px] text-accent hover:text-accent-dark font-medium px-2 py-1"
              >
                Clear All
              </button>
            )}
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-16">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block rounded-xl overflow-hidden border border-text-secondary/10 bg-bg-card">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="bg-bg-secondary/50">
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-text-secondary uppercase tracking-wider w-12">#</th>
                    <th className="px-3 py-3 text-left text-[11px] font-semibold text-text-secondary uppercase tracking-wider">Trader</th>
                    <SortableHeader field="pnl_total" label={tab === 'day' || tab === 'active' ? 'PnL 24h' : 'PnL All'} sortBy={sortBy} onSort={toggleSort} />
                    <th className="px-3 py-3 text-left text-[11px] font-semibold text-text-secondary uppercase tracking-wider">ROI</th>
                    <SortableHeader field="volume" label={tab === 'day' || tab === 'active' ? 'Vol 24h' : 'Vol All'} sortBy={sortBy} onSort={toggleSort} />
                    <th className="px-3 py-3 text-left text-[11px] font-semibold text-text-secondary uppercase tracking-wider">Followers</th>
                    <th className="px-4 py-3 text-right text-[11px] font-semibold text-text-secondary uppercase tracking-wider">Action</th>
                  </tr>
                </thead>
                <tbody className="stagger-children">
                  {filteredLeaders.map((leader, idx) => (
                    <Fragment key={leader.wallet_address}>
                      <tr
                        onClick={() => toggleExpand(leader.wallet_address)}
                        className={clsx(
                          'border-t border-text-secondary/5 cursor-pointer transition-all duration-200',
                          expandedWallet === leader.wallet_address
                            ? 'bg-accent/5'
                            : 'hover:bg-bg-secondary/30',
                        )}
                      >
                        <td className="px-4 py-3.5">
                          <span className={clsx(
                            'text-sm font-bold',
                            leader.rank <= 3 ? 'text-accent' : 'text-text-secondary',
                          )}>
                            {leader.rank <= 3 ? ['', '#1', '#2', '#3'][leader.rank] : leader.rank}
                          </span>
                        </td>
                        <td className="px-3 py-3.5">
                          <div className="flex items-center gap-2.5">
                            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent/20 to-accent-dark/20 flex items-center justify-center border border-accent/10">
                              <span className="text-xs font-bold text-accent">{leader.wallet_address[2]?.toUpperCase()}</span>
                            </div>
                            <div>
                              <div className="text-sm font-medium text-text-primary">
                                {leader.username ? (
                                  <>
                                    <span className="font-semibold">{leader.username}</span>
                                    <span className="text-xs text-text-secondary font-mono ml-1">({shortenAddress(leader.wallet_address)})</span>
                                  </>
                                ) : (
                                  <span className="font-mono">{shortenAddress(leader.wallet_address)}</span>
                                )}
                              </div>
                            </div>
                            <span className={clsx(
                              'text-[10px] transition-transform duration-200',
                              expandedWallet === leader.wallet_address ? 'rotate-90 text-accent' : 'text-text-secondary/40',
                            )}>
                              {'\u25B6'}
                            </span>
                          </div>
                        </td>
                        <td className="px-3 py-3.5">
                          <span className={clsx('text-sm font-bold', leader.pnl_total >= 0 ? 'text-success' : 'text-danger')}>
                            {leader.pnl_total >= 0 ? '+' : ''}{formatUSD(leader.pnl_total)}
                          </span>
                        </td>
                        <td className="px-3 py-3.5">
                          <span className={clsx('text-sm font-medium', leader.roi >= 0 ? 'text-success' : 'text-danger')}>
                            {formatPercent(leader.roi)}
                          </span>
                        </td>
                        <td className="px-3 py-3.5 text-sm text-text-primary">
                          {formatCompact(leader.volume)}
                        </td>
                        <td className="px-3 py-3.5">
                          <div className="flex items-center gap-1.5">
                            <svg className="w-3.5 h-3.5 text-text-secondary/50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                            <span className="text-sm text-text-secondary">{leader.followers_count ?? 0}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3.5 text-right">
                          <div className="flex flex-col items-end gap-1">
                            {followedWallets.has(leader.wallet_address.toLowerCase()) ? (
                              <button
                                onClick={(e) => { e.stopPropagation(); setUnfollowConfirm(leader.wallet_address); }}
                                className="px-4 py-1.5 rounded-lg text-xs font-bold bg-danger/10 text-danger border border-danger/20 hover:bg-danger hover:text-white active:scale-95 transition-all duration-200"
                              >
                                Unfollow
                              </button>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); setFollowLeader(leader); }}
                                className="px-4 py-1.5 rounded-lg text-xs font-bold bg-accent/10 text-accent border border-accent/20 hover:bg-accent hover:text-white hover:shadow-lg hover:shadow-accent/25 active:scale-95 transition-all duration-200"
                              >
                                Follow
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {expandedWallet === leader.wallet_address && (
                        <tr key={`${leader.wallet_address}-detail`}>
                          <td colSpan={7} className="p-0">
                            <PositionDetail walletAddress={leader.wallet_address} isFollowed={followedWallets.has(leader.wallet_address.toLowerCase())} onFollow={() => setFollowLeader(leader)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                  {filteredLeaders.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-3 py-12 text-center text-sm text-text-secondary">
                        {hasActiveFilters ? 'No results match your filters' : tab === 'active' ? 'No active traders in the last 24h' : 'No traders found'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between px-4 py-3 border-t border-text-secondary/10 bg-bg-secondary/30">
              <button
                onClick={() => setPage(Math.max(0, page - 1))}
                disabled={page === 0}
                className="btn-secondary text-xs py-1.5"
              >
                Previous
              </button>
              <span className="text-xs text-text-secondary">
                Page {page + 1}
              </span>
              <button
                onClick={() => setPage(page + 1)}
                disabled={!hasMore}
                className="btn-secondary text-xs py-1.5"
              >
                Next
              </button>
            </div>
          </div>

          {/* Mobile cards */}
          <div className="md:hidden space-y-3 stagger-children">
            {filteredLeaders.map((leader) => (
              <div key={leader.wallet_address}>
                <div onClick={() => toggleExpand(leader.wallet_address)}>
                  <LeaderCard
                    leader={leader}
                    onFollow={() => setFollowLeader(leader)}
                    isFollowed={followedWallets.has(leader.wallet_address.toLowerCase())}
                    onUnfollow={() => setUnfollowConfirm(leader.wallet_address)}
                  />
                </div>
                {expandedWallet === leader.wallet_address && (
                  <div className="mt-1">
                    <PositionDetail walletAddress={leader.wallet_address} isFollowed={followedWallets.has(leader.wallet_address.toLowerCase())} onFollow={() => setFollowLeader(leader)} />
                  </div>
                )}
              </div>
            ))}
            {filteredLeaders.length === 0 && (
              <div className="card text-center py-8 text-sm text-text-secondary">
                {hasActiveFilters ? 'No results match your filters' : tab === 'active' ? 'No active traders in the last 24h' : 'No traders found'}
              </div>
            )}
          </div>
        </>
      )}

      {followLeader && (
        <FollowModal leader={followLeader} onClose={() => setFollowLeader(null)} />
      )}

      {/* Unfollow confirm modal */}
      {unfollowConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setUnfollowConfirm(null)}>
          <div className="bg-bg-card rounded-xl border border-text-secondary/10 p-6 max-w-sm w-full shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold text-text-primary mb-2">Unfollow Trader</h3>
            <p className="text-sm text-text-secondary mb-5">
              Are you sure you want to unfollow <span className="font-mono text-text-primary">{unfollowConfirm.slice(0, 6)}...{unfollowConfirm.slice(-4)}</span>? You will stop receiving trade alerts for this leader.
            </p>
            <div className="flex gap-3">
              <button
                onClick={async () => {
                  try {
                    await unfollow(unfollowConfirm);
                  } catch {}
                  setUnfollowConfirm(null);
                }}
                disabled={isUnfollowing}
                className="flex-1 py-2 rounded-lg text-sm font-semibold bg-danger text-white hover:bg-danger/90 active:scale-95 transition-all"
              >
                {isUnfollowing ? 'Unfollowing...' : 'Yes, Unfollow'}
              </button>
              <button
                onClick={() => setUnfollowConfirm(null)}
                className="flex-1 py-2 rounded-lg text-sm font-semibold bg-bg-secondary text-text-secondary hover:text-text-primary transition-colors"
              >
                No, Keep Following
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
