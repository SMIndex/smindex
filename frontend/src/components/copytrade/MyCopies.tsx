import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { useMarketStore } from '@/stores/marketStore';
import { shortenAddress, formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function MyCopies() {
  const { address, hydrated } = useAuth();
  const markets = useMarketStore((s) => s.markets);

  const { data: copies, isLoading } = useQuery({
    queryKey: ['my-copies', address],
    queryFn: () => api.get('/api/copy/my-copies?status=all&limit=100').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 10000,
  });

  const { data: stats } = useQuery({
    queryKey: ['copy-stats', address],
    queryFn: () => api.get('/api/copy/stats').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 15000,
  });

  if (!hydrated) {
    return <div className="flex justify-center py-12"><div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" /></div>;
  }

  if (!address) {
    return (
      <div className="card text-center py-16 text-sm text-text-secondary">
        <svg className="w-12 h-12 mx-auto mb-3 text-text-secondary/30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
        Connect wallet to view copies
      </div>
    );
  }

  if (isLoading) return <div className="flex justify-center py-12"><LoadingSpinner /></div>;

  const openCopies = (copies || []).filter((c: any) => c.status === 'open');
  const closedCopies = (copies || []).filter((c: any) => c.status === 'closed');

  // Group open copies by leader
  const byLeader: Record<string, any[]> = {};
  for (const c of openCopies) {
    if (!byLeader[c.leader_wallet]) byLeader[c.leader_wallet] = [];
    byLeader[c.leader_wallet].push(c);
  }

  const handleExportCopies = () => {
    api.get('/api/export/copies', { responseType: 'blob' }).then((res) => {
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `copies_${address?.slice(0, 10)}_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
    });
  };

  return (
    <div className="space-y-5">
      {/* Export Button */}
      <div className="flex justify-end">
        <button
          onClick={handleExportCopies}
          className="text-xs px-3 py-1.5 rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-colors font-medium"
        >
          Export CSV
        </button>
      </div>
      {/* Stats Summary */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 stagger-children">
          {[
            { label: 'Total PnL', value: stats.total_pnl, isCurrency: true, colored: true },
            { label: 'Realized', value: stats.total_realized_pnl, isCurrency: true, colored: true },
            { label: 'Unrealized', value: stats.total_unrealized_pnl, isCurrency: true, colored: true },
            { label: 'Active', value: stats.active_copies, isCurrency: false, colored: false },
            { label: 'Closed', value: stats.closed_copies, isCurrency: false, colored: false },
          ].map((s) => (
            <div key={s.label} className="card-glow py-3 animate-pulseGlow" style={{ animationDelay: '0s' }}>
              <div className="text-[10px] uppercase tracking-widest text-text-secondary mb-1">{s.label}</div>
              <div className={clsx(
                'text-base sm:text-lg font-bold animate-countUp truncate',
                s.colored ? (s.value >= 0 ? 'text-success' : 'text-danger') : 'text-text-primary',
              )}>
                {s.isCurrency ? `${s.value >= 0 ? '+' : ''}${formatUSD(s.value)}` : s.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Per-Leader Stats */}
      {stats?.per_leader?.length > 0 && (
        <div className="rounded-xl overflow-hidden border border-text-secondary/10 bg-bg-card animate-fadeInUp">
          <div className="px-4 py-2.5 bg-bg-secondary/50 text-[11px] font-semibold text-text-secondary uppercase tracking-widest">
            PnL by Leader
          </div>
          <div className="stagger-children">
            {stats.per_leader.map((ls: any) => (
              <div key={ls.leader_wallet} className="flex flex-wrap items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 border-t border-text-secondary/5 hover:bg-bg-secondary/20 transition-colors text-xs">
                <div className="w-6 h-6 rounded-md bg-gradient-to-br from-accent/20 to-accent-dark/20 flex items-center justify-center shrink-0">
                  <span className="text-[9px] font-bold text-accent">{ls.leader_wallet[2]?.toUpperCase()}</span>
                </div>
                <span className="font-mono text-accent text-xs truncate max-w-[80px] sm:max-w-none">{shortenAddress(ls.leader_wallet)}</span>
                <span className={clsx('font-bold', ls.pnl >= 0 ? 'text-success' : 'text-danger')}>
                  {ls.pnl >= 0 ? '+' : ''}{formatUSD(ls.pnl)}
                </span>
                <span className="text-text-secondary">{ls.trades} trades</span>
                <div className="ml-auto flex items-center gap-1">
                  <div className="w-12 h-1.5 bg-bg-secondary rounded-full overflow-hidden">
                    <div className="h-full bg-accent rounded-full" style={{ width: `${ls.win_rate}%` }} />
                  </div>
                  <span className="text-text-secondary text-[10px]">{ls.win_rate}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active Copies grouped by leader */}
      {Object.entries(byLeader).length > 0 ? (
        <div className="space-y-4 stagger-children">
          {Object.entries(byLeader).map(([leader, positions]) => (
            <div key={leader} className="rounded-xl overflow-hidden border border-accent/10 bg-bg-card animate-fadeInUp">
              <div className="px-4 py-3 bg-gradient-to-r from-accent/5 to-transparent flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-accent/20 to-accent-dark/20 flex items-center justify-center">
                  <span className="text-[10px] font-bold text-accent">{leader[2]?.toUpperCase()}</span>
                </div>
                <span className="text-xs font-mono text-accent font-medium">{shortenAddress(leader)}</span>
                <span className="text-[10px] px-2 py-0.5 bg-accent/10 text-accent rounded-full font-medium">{positions.length} active</span>
              </div>
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-secondary/70 bg-bg-secondary/30">
                      <th className="px-4 py-2 text-left font-medium">Market</th>
                      <th className="px-2 py-2 text-left font-medium">Size</th>
                      <th className="px-2 py-2 text-left font-medium">Entry</th>
                      <th className="px-2 py-2 text-right font-medium">PnL</th>
                      <th className="px-4 py-2 text-right font-medium">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((pos: any) => {
                      const cfg = MARKETS[pos.market_id];
                      const dec = cfg?.decimals ?? 2;
                      const m = markets[pos.market_id];
                      const markPrice = m?.mark_price ?? pos.mark_price ?? pos.entry_price;
                      const livePnl = pos.unrealized_pnl ?? (
                        pos.side === 'long'
                          ? (markPrice - pos.entry_price) * pos.size
                          : (pos.entry_price - markPrice) * pos.size
                      );
                      return (
                        <tr key={pos.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/20 transition-colors">
                          <td className="px-4 py-2.5">
                            <span className="font-bold text-text-primary">{pos.symbol}</span>
                            <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                              {pos.side} {pos.leverage}x
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-text-primary">{pos.size.toFixed(cfg?.sizeDecimals ?? 4)}</td>
                          <td className="px-2 py-2.5 text-text-primary">${formatPrice(pos.entry_price, dec)}</td>
                          <td className="px-2 py-2.5 text-right">
                            <span className={clsx('font-bold', livePnl >= 0 ? 'text-success' : 'text-danger')}>
                              {livePnl >= 0 ? '+' : ''}{formatUSD(livePnl)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right">
                            <span className="text-[10px] px-1.5 py-0.5 bg-accent/10 text-accent rounded font-medium">{pos.source}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="md:hidden">
                {positions.map((pos: any) => {
                  const cfg = MARKETS[pos.market_id];
                  const dec = cfg?.decimals ?? 2;
                  const m = markets[pos.market_id];
                  const markPrice = m?.mark_price ?? pos.mark_price ?? pos.entry_price;
                  const livePnl = pos.unrealized_pnl ?? (
                    pos.side === 'long'
                      ? (markPrice - pos.entry_price) * pos.size
                      : (pos.entry_price - markPrice) * pos.size
                  );
                  return (
                    <div key={pos.id} className="px-3 py-2 border-t border-text-secondary/5 text-[11px]">
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="font-bold text-text-primary">{pos.symbol}</span>
                          <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                            {pos.side} {pos.leverage}x
                          </span>
                        </div>
                        <span className={clsx('font-bold', livePnl >= 0 ? 'text-success' : 'text-danger')}>
                          {livePnl >= 0 ? '+' : ''}{formatUSD(livePnl)}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                        <span>Size: {pos.size.toFixed(cfg?.sizeDecimals ?? 4)}</span>
                        <span>Entry: ${formatPrice(pos.entry_price, dec)}</span>
                        <span className="text-accent">{pos.source}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="card text-center py-16 animate-fadeIn">
          <svg className="w-16 h-16 mx-auto mb-4 text-accent/20" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2" />
          </svg>
          <div className="text-sm text-text-secondary mb-1">No active copy positions</div>
          <div className="text-xs text-text-secondary/60">Copy a trade from the Leaderboard to get started</div>
        </div>
      )}

      {/* Closed Copies */}
      {closedCopies.length > 0 && (
        <div className="rounded-xl overflow-hidden border border-text-secondary/10 bg-bg-card animate-fadeInUp">
          <div className="px-4 py-2.5 bg-bg-secondary/50 flex items-center justify-between">
            <span className="text-[11px] font-semibold text-text-secondary uppercase tracking-widest">Closed Copies</span>
            <span className="text-[10px] text-text-secondary/60">{closedCopies.length} total</span>
          </div>
          <div className="max-h-72 overflow-y-auto stagger-children">
            {closedCopies.map((pos: any) => {
              const cfg = MARKETS[pos.market_id];
              const dec = cfg?.decimals ?? 2;
              return (
                <div key={pos.id} className="flex flex-wrap items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2.5 border-t border-text-secondary/5 hover:bg-bg-secondary/20 transition-colors text-[11px]">
                  <span className="font-bold text-text-primary">{pos.symbol}</span>
                  <span className={clsx('font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded', pos.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>{pos.side}</span>
                  <span className="text-text-secondary">${formatPrice(pos.entry_price, dec)}</span>
                  <svg className="w-3 h-3 text-text-secondary/30 hidden sm:block" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
                  </svg>
                  <span className="text-text-secondary">${formatPrice(pos.close_price, dec)}</span>
                  <span className={clsx('font-bold ml-auto', (pos.realized_pnl || 0) >= 0 ? 'text-success' : 'text-danger')}>
                    {(pos.realized_pnl || 0) >= 0 ? '+' : ''}{formatUSD(pos.realized_pnl || 0)}
                  </span>
                  <span className="text-text-secondary/40 text-[10px] w-20 text-right">{pos.closed_at ? new Date(pos.closed_at).toLocaleDateString() : ''}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
