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

function MarketRow({ m }: { m: any }) {
  const isUp = m.change_24h_pct >= 0;
  return (
    <tr className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{m.symbol}</span>
          <span className="text-[10px] text-text-secondary">/USD</span>
        </div>
      </td>
      <td className="px-3 py-3 text-right">
        <div className="text-sm font-semibold text-text-primary">${m.mark_price.toLocaleString()}</div>
      </td>
      <td className="px-3 py-3 text-right">
        <span className={clsx('text-xs font-semibold', isUp ? 'text-success' : 'text-danger')}>
          {isUp ? '+' : ''}{m.change_24h_pct}%
        </span>
      </td>
      <td className="px-3 py-3 text-right text-sm text-text-primary">{formatCompact(m.volume_24h_usd)}</td>
      <td className="px-3 py-3 text-right text-sm text-text-primary">{formatCompact(m.open_interest_usd)}</td>
      <td className="px-3 py-3 text-right text-sm text-text-primary">{formatCompact(m.tvl)}</td>
      <td className="px-3 py-3 text-right">
        <div className={clsx('text-xs font-semibold',
          m.funding_rate > 0 ? 'text-success' : m.funding_rate < 0 ? 'text-danger' : 'text-text-secondary'
        )}>
          {m.funding_rate_pct > 0 ? '+' : ''}{m.funding_rate_pct.toFixed(4)}%
        </div>
        <div className="text-[9px] text-text-secondary">{m.funding_direction}</div>
      </td>
      <td className="px-3 py-3 text-right text-xs text-text-secondary">{m.spread_pct.toFixed(3)}%</td>
    </tr>
  );
}

export default function PerplReport() {
  const { data, isLoading } = useQuery({
    queryKey: ['perpl-report'],
    queryFn: () => api.get('/api/perpl-report').then((r) => r.data),
    refetchInterval: 30000,
  });

  if (isLoading || !data) {
    return (
      <div className="flex justify-center py-20"><LoadingSpinner /></div>
    );
  }

  const { platform, markets, leaderboard: lb } = data;

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
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Perpl Platform Report</h1>
          <p className="text-sm text-text-secondary mt-1">Live data from Perpl perpetual futures on Monad</p>
        </div>
        <div className="text-[10px] text-text-secondary bg-bg-secondary px-3 py-1.5 rounded-lg">
          Auto-refreshes every 30s
        </div>
      </div>

      {/* Platform Overview Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Total Value Locked"
          value={formatCompact(platform.total_tvl)}
          color="text-accent"
        />
        <StatCard
          label="Open Interest"
          value={formatCompact(platform.total_open_interest_usd)}
        />
        <StatCard
          label="24h Volume"
          value={formatCompact(platform.total_volume_24h)}
          color="text-success"
        />
        <StatCard
          label="Total Traders"
          value={lb.total_traders.toLocaleString()}
          sub={`${lb.active_24h} active in 24h`}
        />
      </div>

      {/* Trader Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatCard
          label="All-Time Volume"
          value={formatCompact(lb.total_volume_all_time)}
        />
        <StatCard
          label="Profitable Traders"
          value={lb.profitable_traders.toString()}
          sub={`${lb.total_traders > 0 ? Math.round(lb.profitable_traders / lb.total_traders * 100) : 0}% of all traders`}
          color="text-success"
        />
        <StatCard
          label="Losing Traders"
          value={lb.losing_traders.toString()}
          sub={`${lb.total_traders > 0 ? Math.round(lb.losing_traders / lb.total_traders * 100) : 0}% of all traders`}
          color="text-danger"
        />
      </div>

      {/* Markets Table */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-text-secondary/10">
          <h2 className="text-sm font-semibold text-text-primary">Markets</h2>
        </div>
        {/* Desktop */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-text-secondary border-b border-text-secondary/10">
                <th className="px-4 py-2.5 text-left font-medium">Market</th>
                <th className="px-3 py-2.5 text-right font-medium">Price</th>
                <th className="px-3 py-2.5 text-right font-medium">24h Change</th>
                <th className="px-3 py-2.5 text-right font-medium">24h Volume</th>
                <th className="px-3 py-2.5 text-right font-medium">Open Interest</th>
                <th className="px-3 py-2.5 text-right font-medium">TVL</th>
                <th className="px-3 py-2.5 text-right font-medium">Funding Rate</th>
                <th className="px-3 py-2.5 text-right font-medium">Spread</th>
              </tr>
            </thead>
            <tbody>
              {markets.map((m: any) => <MarketRow key={m.market_id} m={m} />)}
            </tbody>
          </table>
        </div>
        {/* Mobile */}
        <div className="md:hidden">
          {markets.map((m: any) => {
            const isUp = m.change_24h_pct >= 0;
            return (
              <div key={m.market_id} className="px-4 py-3 border-t border-text-secondary/5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-text-primary">{m.symbol}</span>
                    <span className={clsx('text-xs font-semibold', isUp ? 'text-success' : 'text-danger')}>
                      {isUp ? '+' : ''}{m.change_24h_pct}%
                    </span>
                  </div>
                  <span className="font-semibold text-text-primary">${m.mark_price.toLocaleString()}</span>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-1.5 text-[10px] text-text-secondary">
                  <span>Vol: {formatCompact(m.volume_24h_usd)}</span>
                  <span>OI: {formatCompact(m.open_interest_usd)}</span>
                  <span>TVL: {formatCompact(m.tvl)}</span>
                  <span className={m.funding_rate > 0 ? 'text-success' : m.funding_rate < 0 ? 'text-danger' : ''}>
                    Funding: {m.funding_rate_pct > 0 ? '+' : ''}{m.funding_rate_pct.toFixed(4)}%
                  </span>
                  <span>Spread: {m.spread_pct.toFixed(3)}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Top Traders */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Top by PnL */}
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Top Traders by PnL</h2>
          </div>
          <div>
            {lb.top_by_pnl.map((t: any, i: number) => (
              <div key={t.wallet} className="flex items-center justify-between px-4 py-2.5 border-t border-text-secondary/5 text-[11px]">
                <div className="flex items-center gap-3">
                  <span className={clsx('text-xs font-bold w-5', i < 3 ? 'text-accent' : 'text-text-secondary')}>
                    #{t.rank}
                  </span>
                  <span className="font-mono text-text-primary">{shortenAddress(t.wallet)}</span>
                </div>
                <div className="text-right">
                  <div className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                    {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                  </div>
                  <div className="text-[9px] text-text-secondary">Vol: {formatCompact(t.volume)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Top by Volume */}
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Top Traders by Volume</h2>
          </div>
          <div>
            {lb.top_by_volume.map((t: any, i: number) => (
              <div key={t.wallet} className="flex items-center justify-between px-4 py-2.5 border-t border-text-secondary/5 text-[11px]">
                <div className="flex items-center gap-3">
                  <span className={clsx('text-xs font-bold w-5', i < 3 ? 'text-accent' : 'text-text-secondary')}>
                    #{t.rank}
                  </span>
                  <span className="font-mono text-text-primary">{shortenAddress(t.wallet)}</span>
                </div>
                <div className="text-right">
                  <div className="font-bold text-text-primary">{formatCompact(t.volume)}</div>
                  <div className={clsx('text-[9px] font-semibold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                    PnL: {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="text-center text-[10px] text-text-secondary py-4">
        Data sourced from Perpl public API (app.perpl.xyz) | Monad Mainnet (Chain ID: 143)
      </div>
      </div>
    </div>
  );
}
