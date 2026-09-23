import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { formatUSD, formatCompact, shortenAddress } from '@/lib/formatters';
import LoadingSpinner from '@/components/common/LoadingSpinner';

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="card p-4">
      <div className="text-[10px] text-text-secondary uppercase tracking-wider font-medium">{label}</div>
      <div className={clsx('text-xl font-bold mt-1', color || 'text-text-primary')}>{value}</div>
      {sub && <div className="text-[10px] text-text-secondary mt-0.5">{sub}</div>}
    </div>
  );
}

function ProgressBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min(value / max * 100, 100) : 0;
  return (
    <div className="w-full h-1.5 bg-bg-secondary rounded-full overflow-hidden">
      <div className={clsx('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function TerminalStats() {
  const { data, isLoading } = useQuery({
    queryKey: ['terminal-stats'],
    queryFn: () => api.get('/api/terminal-stats').then((r) => r.data),
    refetchInterval: 30000,
  });

  if (isLoading || !data) {
    return <div className="flex justify-center py-20"><LoadingSpinner /></div>;
  }

  const { users, trading, orders, copy_trading: copy, daily_activity: daily } = data;
  const maxDailyVol = Math.max(...(daily.map((d: any) => d.volume) || [0]), 1);

  return (
    <div className="min-h-screen bg-bg-primary text-text-primary">
      {/* Top bar */}
      <div className="sticky top-0 z-10 bg-bg-primary/95 backdrop-blur border-b border-text-secondary/10 px-4 py-3">
        <Link to="/trade" className="inline-flex items-center gap-2 text-sm font-medium text-accent hover:text-accent/80 transition-colors">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Take me to trading window
        </Link>
      </div>

      <div className="space-y-6 max-w-6xl mx-auto p-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-text-primary">Terminal Statistics</h1>
        <p className="text-sm text-text-secondary mt-1">Platform metrics for SMINDEX</p>
      </div>

      {/* Users */}
      <div>
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">Users</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Total Users" value={users.total.toString()} color="text-accent" />
          <StatCard label="Perpl Linked" value={users.perpl_linked.toString()} sub={`${users.total > 0 ? Math.round(users.perpl_linked / users.total * 100) : 0}% of total`} />
          <StatCard label="With Username" value={users.with_username.toString()} />
          <StatCard label="Active (7d)" value={users.active_7d.toString()} color="text-success" />
        </div>
      </div>

      {/* Trading */}
      <div>
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">Trading</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Total Trades" value={trading.total_trades.toString()} />
          <StatCard
            label="Total Volume"
            value={trading.total_volume > 0 ? formatCompact(trading.total_volume) : '$0'}
            color="text-accent"
          />
          <StatCard
            label="Avg Trade Size"
            value={trading.avg_trade_size > 0 ? formatUSD(trading.avg_trade_size) : '$0'}
          />
          <StatCard
            label="Total Realized PnL"
            value={(trading.total_pnl >= 0 ? '+' : '') + formatUSD(trading.total_pnl)}
            color={trading.total_pnl >= 0 ? 'text-success' : 'text-danger'}
          />
        </div>
      </div>

      {/* Volume by Market */}
      {trading.by_market.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Volume by Market</h2>
          </div>
          <div className="divide-y divide-text-secondary/5">
            {trading.by_market.map((m: any) => (
              <div key={m.market_id} className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-bold text-text-primary w-10">{m.symbol}</span>
                  <div className="w-32 hidden sm:block">
                    <ProgressBar
                      value={m.volume}
                      max={Math.max(...trading.by_market.map((x: any) => x.volume))}
                      color="bg-accent"
                    />
                  </div>
                </div>
                <div className="flex items-center gap-6 text-sm">
                  <div className="text-right">
                    <div className="text-text-primary font-semibold">{formatCompact(m.volume)}</div>
                    <div className="text-[10px] text-text-secondary">volume</div>
                  </div>
                  <div className="text-right w-12">
                    <div className="text-text-primary font-semibold">{m.trades}</div>
                    <div className="text-[10px] text-text-secondary">trades</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trade Source Split */}
      {Object.keys(trading.by_source).length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Object.entries(trading.by_source).map(([source, stats]: [string, any]) => (
            <div key={source} className="card p-4">
              <div className="flex items-center justify-between">
                <div>
                  <span className={clsx('text-xs font-semibold px-2 py-0.5 rounded',
                    source === 'copy_trade' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary'
                  )}>
                    {source === 'copy_trade' ? 'Social Trades' : 'Manual Trades'}
                  </span>
                </div>
                <div className="text-right">
                  <div className="text-lg font-bold text-text-primary">{stats.count}</div>
                  <div className="text-[10px] text-text-secondary">{formatCompact(stats.volume)} volume</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Orders */}
      <div>
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">Orders</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Total Orders" value={orders.total.toString()} />
          <StatCard
            label="Fill Rate"
            value={`${orders.fill_rate}%`}
            color={orders.fill_rate >= 80 ? 'text-success' : orders.fill_rate >= 50 ? 'text-warning' : 'text-danger'}
          />
          <StatCard label="Market Orders" value={(orders.by_type.market || 0).toString()} />
          <StatCard label="Limit Orders" value={(orders.by_type.limit || 0).toString()} />
        </div>

        {/* Order Status Breakdown */}
        {Object.keys(orders.by_status).length > 0 && (
          <div className="flex gap-2 mt-3 flex-wrap">
            {Object.entries(orders.by_status).map(([status, count]: [string, any]) => (
              <div key={status} className={clsx(
                'px-3 py-1.5 rounded-lg text-xs font-medium',
                status === 'filled' ? 'bg-success/10 text-success' :
                status === 'failed' ? 'bg-danger/10 text-danger' :
                status === 'open' ? 'bg-accent/10 text-accent' :
                'bg-warning/10 text-warning'
              )}>
                {status}: {count}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Copy Trading */}
      <div>
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider mb-3">Copy Trading</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <StatCard label="Total Copy Positions" value={copy.total_copies.toString()} />
          <StatCard label="Copy Volume" value={copy.copy_volume > 0 ? formatCompact(copy.copy_volume) : '$0'} />
          <StatCard label="Active Followers" value={copy.active_followers.toString()} color="text-accent" />
        </div>

        {/* Top Leaders */}
        {copy.top_leaders.length > 0 && (
          <div className="card p-0 overflow-hidden mt-3">
            <div className="px-4 py-3 border-b border-text-secondary/10">
              <h3 className="text-xs font-semibold text-text-primary">Most Followed Leaders</h3>
            </div>
            {copy.top_leaders.map((l: any, i: number) => (
              <div key={l.wallet} className="flex items-center justify-between px-4 py-2.5 border-t border-text-secondary/5 text-[11px]">
                <div className="flex items-center gap-3">
                  <span className={clsx('text-xs font-bold w-5', i < 3 ? 'text-accent' : 'text-text-secondary')}>#{i + 1}</span>
                  <span className="font-mono text-text-primary">{shortenAddress(l.wallet)}</span>
                </div>
                <span className="text-text-primary font-semibold">{l.followers} follower{l.followers !== 1 ? 's' : ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Daily Activity */}
      {daily.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Daily Activity (Last 30 Days)</h2>
          </div>
          <div className="px-4 py-3">
            {/* Simple bar chart */}
            <div className="flex items-end gap-1 h-24">
              {daily.map((d: any) => {
                const height = maxDailyVol > 0 ? Math.max((d.volume / maxDailyVol) * 100, 2) : 2;
                return (
                  <div
                    key={d.date}
                    className="flex-1 bg-accent/60 rounded-t hover:bg-accent transition-colors group relative"
                    style={{ height: `${height}%` }}
                    title={`${d.date}: ${d.trades} trades, ${formatUSD(d.volume)} vol, ${d.active_users} users`}
                  />
                );
              })}
            </div>
            {/* Date labels */}
            <div className="flex justify-between mt-1">
              <span className="text-[9px] text-text-secondary">{daily[0]?.date}</span>
              <span className="text-[9px] text-text-secondary">{daily[daily.length - 1]?.date}</span>
            </div>
          </div>
          {/* Summary row */}
          <div className="flex items-center justify-around px-4 py-2.5 border-t border-text-secondary/10 text-[10px] text-text-secondary">
            <span>Total: {daily.reduce((s: number, d: any) => s + d.trades, 0)} trades</span>
            <span>Volume: {formatCompact(daily.reduce((s: number, d: any) => s + d.volume, 0))}</span>
            <span>Peak: {Math.max(...daily.map((d: any) => d.trades))} trades/day</span>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-center text-[10px] text-text-secondary py-4">
        SMINDEX — smindex.xyz
      </div>
      </div>
    </div>
  );
}
