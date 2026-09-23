import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getLeader, getLeaderTrades } from '@/lib/api';
import {
  shortenAddress,
  formatUSD,
  formatPercent,
  formatTimeAgo,
  formatPrice,
} from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import { useAuth } from '@/hooks/useAuth';
import { useCopyTrading } from '@/hooks/useCopyTrading';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import FollowModal from '@/components/copytrade/FollowModal';
import LeaderPerformanceChart from '@/components/copytrade/LeaderPerformanceChart';
import { useState } from 'react';

interface LeaderProfileProps {
  leaderId: string;
}

export default function LeaderProfile({ leaderId }: LeaderProfileProps) {
  const { isAuthenticated } = useAuth();
  const { follows } = useCopyTrading();
  const [showFollowModal, setShowFollowModal] = useState(false);

  const {
    data: leader,
    isLoading: leaderLoading,
  } = useQuery({
    queryKey: ['leader', leaderId],
    queryFn: () => getLeader(leaderId),
  });

  const { data: trades, isLoading: tradesLoading } = useQuery({
    queryKey: ['leader-trades', leaderId],
    queryFn: () => getLeaderTrades(leaderId, { limit: 50 }),
  });

  const isFollowing = follows.some((f) => f.leader_id === leaderId);

  if (leaderLoading) {
    return (
      <div className="flex justify-center py-16">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!leader) {
    return (
      <div className="card text-center py-12">
        <p className="text-text-secondary">Leader not found</p>
      </div>
    );
  }

  const stats = leader.stats || {
    pnl_total: 0, roi: 0, volume: 0, pnl_7d: 0, pnl_30d: 0,
    win_rate: 0, total_trades: 0, sharpe_ratio: 0, max_drawdown: 0,
    avg_leverage: 0, followers_count: 0,
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="card">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-xl bg-accent/10 flex items-center justify-center">
              <span className="text-xl font-bold text-accent">
                {leader.wallet_address[2].toUpperCase()}
              </span>
            </div>
            <div>
              <h1 className="text-xl font-bold text-text-primary">
                {leader.display_name || shortenAddress(leader.wallet_address)}
              </h1>
              <div className="text-sm text-text-secondary font-mono mt-0.5">
                {shortenAddress(leader.wallet_address)}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span
                  className={clsx(
                    'text-xs px-2 py-0.5 rounded',
                    leader.is_active
                      ? 'text-success bg-success/10'
                      : 'text-text-secondary bg-bg-secondary',
                  )}
                >
                  {leader.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            </div>
          </div>

          {isAuthenticated && (
            <button
              onClick={() => setShowFollowModal(true)}
              className={clsx(
                isFollowing ? 'btn-secondary' : 'btn-primary',
              )}
            >
              {isFollowing ? 'Following' : 'Follow'}
            </button>
          )}
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          {
            label: 'Total PnL',
            value: formatUSD(stats.pnl_total),
            color: stats.pnl_total >= 0 ? 'text-success' : 'text-danger',
          },
          {
            label: 'ROI',
            value: formatPercent(stats.roi || 0),
            color: (stats.roi || 0) >= 0 ? 'text-success' : 'text-danger',
          },
          {
            label: 'Volume',
            value: formatUSD(stats.volume || 0),
            color: 'text-text-primary',
          },
          {
            label: 'Win Rate',
            value: formatPercent(stats.win_rate),
            color: 'text-text-primary',
          },
          {
            label: 'Total Trades',
            value: stats.total_trades.toString(),
            color: 'text-text-primary',
          },
          {
            label: 'Sharpe Ratio',
            value: (stats.sharpe_ratio || 0).toFixed(2),
            color: 'text-text-primary',
          },
          {
            label: 'Max Drawdown',
            value: formatPercent(-(stats.max_drawdown || 0)),
            color: 'text-danger',
          },
          {
            label: 'Avg Leverage',
            value: `${(stats.avg_leverage || 0).toFixed(1)}x`,
            color: 'text-text-primary',
          },
          {
            label: 'Followers',
            value: (stats.followers_count || 0).toString(),
            color: 'text-accent',
          },
        ].map((stat) => (
          <div key={stat.label} className="card">
            <div className="text-xs text-text-secondary mb-1">{stat.label}</div>
            <div className={clsx('text-lg font-bold', stat.color)}>
              {stat.value}
            </div>
          </div>
        ))}
      </div>

      {/* Performance chart */}
      {leader?.wallet_address && (
        <LeaderPerformanceChart walletAddress={leader.wallet_address} />
      )}

      {/* Recent trades */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-text-secondary/10">
          <h2 className="text-lg font-semibold text-text-primary">
            Recent Trades
          </h2>
        </div>

        {tradesLoading ? (
          <div className="flex justify-center py-8">
            <LoadingSpinner />
          </div>
        ) : !trades || trades.length === 0 ? (
          <div className="text-sm text-text-secondary text-center py-8">
            No trades recorded
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-text-secondary/10">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Market
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Side
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Size
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Price
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Leverage
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-text-secondary">
                    Type
                  </th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-text-secondary">
                    Time
                  </th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => {
                  const mkt = MARKETS[trade.market_id];
                  return (
                    <tr
                      key={trade.id}
                      className="border-b border-text-secondary/5"
                    >
                      <td className="px-4 py-2 text-sm text-text-primary font-medium">
                        {mkt?.symbol ?? trade.market_id}
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={clsx(
                            'text-xs font-semibold uppercase px-1.5 py-0.5 rounded',
                            trade.side === 'buy'
                              ? 'text-success bg-success/10'
                              : 'text-danger bg-danger/10',
                          )}
                        >
                          {trade.side}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-sm text-text-primary">
                        {trade.size.toFixed(4)}
                      </td>
                      <td className="px-4 py-2 text-sm text-text-primary">
                        ${formatPrice(trade.price, mkt?.decimals ?? 2)}
                      </td>
                      <td className="px-4 py-2 text-sm text-text-primary">
                        {trade.leverage}x
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={clsx(
                            'text-xs px-1.5 py-0.5 rounded',
                            trade.is_close
                              ? 'text-warning bg-warning/10'
                              : 'text-accent bg-accent/10',
                          )}
                        >
                          {trade.is_close ? 'Close' : 'Open'}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-xs text-text-secondary text-right">
                        {formatTimeAgo(trade.timestamp)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showFollowModal && (
        <FollowModal
          leader={leader}
          onClose={() => setShowFollowModal(false)}
        />
      )}
    </div>
  );
}
