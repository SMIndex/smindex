import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useMarketData } from '@/hooks/useMarketData';
import { useWhaleAlerts } from '@/hooks/useWhaleAlerts';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { formatCompact, formatPrice, formatPercent, formatUSD, shortenAddress } from '@/lib/formatters';
import api, { getPnlCard, getFollowedLeadersPositions, type FollowedLeader, type FollowedLeaderPosition } from '@/lib/api';
import { getTraderPositions } from '@/lib/api';
import MarketTicker from '@/components/dashboard/MarketTicker';
import WhaleAlertCard from '@/components/whales/WhaleAlertCard';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import CopyModal from '@/components/copytrade/CopyModal';

const COIN_GRADIENTS: Record<string, string> = {
  BTC: 'from-orange-500/10 to-orange-600/5 border-orange-500/20',
  ETH: 'from-blue-500/10 to-blue-600/5 border-blue-500/20',
  MON: 'from-purple-500/10 to-purple-600/5 border-purple-500/20',
  SOL: 'from-emerald-500/10 to-emerald-600/5 border-emerald-500/20',
};

const ONBOARDING_KEY = 'perpl-onboarding-dismissed';

function OnboardingSection() {
  const [dismissed, setDismissed] = useState(() => {
    return localStorage.getItem(ONBOARDING_KEY) === 'true';
  });

  if (dismissed) return null;

  const handleDismiss = () => {
    localStorage.setItem(ONBOARDING_KEY, 'true');
    setDismissed(true);
  };

  const steps = [
    {
      title: 'Connect your wallet',
      desc: 'Sign in with your Ethereum wallet to unlock all features',
      to: '#',
      icon: 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
      bgClass: 'bg-accent/10 group-hover:bg-accent/20',
      textClass: 'text-accent',
    },
    {
      title: 'Explore the terminal',
      desc: 'View charts, orderbook, and market data in real time',
      to: '/trade',
      icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
      bgClass: 'bg-success/10 group-hover:bg-success/20',
      textClass: 'text-success',
    },
    {
      title: 'Follow a trader or place a trade',
      desc: 'Copy top traders or execute your own perpetual futures trades',
      to: '/copy',
      icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z',
      bgClass: 'bg-warning/10 group-hover:bg-warning/20',
      textClass: 'text-warning',
    },
    {
      title: 'Set up alerts',
      desc: 'Get notified about price moves, whale activity, and funding changes',
      to: '/settings',
      icon: 'M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9',
      bgClass: 'bg-danger/10 group-hover:bg-danger/20',
      textClass: 'text-danger',
    },
  ];

  return (
    <div className="relative bg-gradient-to-br from-accent/5 via-bg-secondary to-bg-card border border-accent/15 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-accent/10">
        <div className="flex items-center gap-2">
          <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
          <h2 className="text-sm font-semibold text-text-primary">Getting Started</h2>
        </div>
        <button
          onClick={handleDismiss}
          className="w-6 h-6 rounded-md flex items-center justify-center text-text-secondary hover:text-text-primary hover:bg-text-secondary/10 transition-all"
          title="Dismiss"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 p-4">
        {steps.map((step, i) => (
          <Link
            key={i}
            to={step.to}
            className="group flex items-start gap-3 p-3 rounded-lg hover:bg-bg-primary/50 transition-all"
          >
            <div className={clsx('shrink-0 p-2 rounded-lg transition-colors', step.bgClass)}>
              <svg className={clsx('w-4 h-4', step.textClass)} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={step.icon} />
              </svg>
            </div>
            <div>
              <div className="text-xs font-semibold text-text-primary group-hover:text-accent transition-colors">
                <span className="text-text-secondary/40 mr-1">{i + 1}.</span>
                {step.title}
              </div>
              <div className="text-[10px] text-text-secondary mt-0.5 leading-relaxed">{step.desc}</div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function QuickActions() {
  const actions = [
    { to: '/trade', label: 'Trade', icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6', classes: 'bg-accent/10 text-accent hover:bg-accent/20' },
    { to: '/copy', label: 'Copy Trade', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z', classes: 'bg-success/10 text-success hover:bg-success/20' },
    { to: '/portfolio', label: 'Portfolio', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', classes: 'bg-warning/10 text-warning hover:bg-warning/20' },
    { to: '/heatmap', label: 'Heatmap', icon: 'M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z', classes: 'bg-danger/10 text-danger hover:bg-danger/20' },
  ];

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {actions.map((a) => (
        <Link
          key={a.to}
          to={a.to}
          className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
            a.classes,
          )}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={a.icon} />
          </svg>
          {a.label}
        </Link>
      ))}
    </div>
  );
}

function UserPositions({ address }: { address: string }) {
  const { data: positions, isLoading } = useQuery({
    queryKey: ['my-positions', address],
    queryFn: () => getTraderPositions(address),
    enabled: !!address,
    refetchInterval: 15000,
  });

  if (isLoading) return <div className="flex justify-center py-6"><LoadingSpinner /></div>;

  const openPositions = (positions?.positions || []).filter((p: any) => p.deposit > 0);

  if (openPositions.length === 0) {
    return (
      <div className="text-center py-6">
        <p className="text-sm text-text-secondary">No open positions</p>
        <Link to="/trade" className="text-xs text-accent hover:text-accent/80 mt-1 inline-block">Open Terminal</Link>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {openPositions.map((pos: any, i: number) => {
        const mktConfig = MARKETS[pos.market_id];
        const symbol = mktConfig?.symbol || `MKT-${pos.market_id}`;
        const isLong = pos.side === 'long';
        const pnl = pos.pnl ?? 0;
        const pnlPct = pos.deposit > 0 ? (pnl / pos.deposit) * 100 : 0;

        return (
          <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-bg-primary/50 hover:bg-bg-primary/80 transition-colors">
            <div className="flex items-center gap-3">
              <span className={clsx(
                'text-[10px] font-bold px-1.5 py-0.5 rounded',
                isLong ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger'
              )}>
                {isLong ? 'LONG' : 'SHORT'}
              </span>
              <div>
                <span className="text-sm font-medium text-text-primary">{symbol}</span>
                <span className="text-[10px] text-text-secondary ml-2">
                  {pos.leverage ? `${pos.leverage}x` : ''}
                </span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-xs text-text-secondary">Size: {formatUSD(pos.notional || pos.size * pos.entry_price)}</div>
              <div className={clsx('text-xs font-medium', pnl >= 0 ? 'text-success' : 'text-danger')}>
                {pnl >= 0 ? '+' : ''}{formatUSD(pnl)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RecentCopyTrades({ address }: { address: string }) {
  const { data: copies, isLoading } = useQuery({
    queryKey: ['dashboard-copies', address],
    queryFn: () => api.get('/api/copy/my-copies?status=all&limit=5').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 15000,
  });

  if (isLoading) return <div className="flex justify-center py-6"><LoadingSpinner /></div>;

  if (!copies || copies.length === 0) {
    return (
      <div className="text-center py-6">
        <p className="text-sm text-text-secondary">No copy trades yet</p>
        <Link to="/copy" className="text-xs text-accent hover:text-accent/80 mt-1 inline-block">Browse Traders</Link>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {copies.slice(0, 5).map((c: any, i: number) => {
        const mktConfig = MARKETS[c.market_id];
        const symbol = mktConfig?.symbol || `MKT-${c.market_id}`;
        const isOpen = c.status === 'open';

        return (
          <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-bg-primary/50">
            <div className="flex items-center gap-2">
              <span className={clsx('w-1.5 h-1.5 rounded-full', isOpen ? 'bg-success' : 'bg-text-secondary/30')} />
              <div>
                <span className="text-xs font-medium text-text-primary">{symbol}</span>
                <span className={clsx('text-[10px] ml-1.5', c.side === 'long' ? 'text-success' : 'text-danger')}>
                  {c.side?.toUpperCase()}
                </span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-text-secondary">
                Copying {shortenAddress(c.leader_wallet)}
              </div>
              {c.pnl !== undefined && (
                <div className={clsx('text-[10px] font-medium', c.pnl >= 0 ? 'text-success' : 'text-danger')}>
                  {c.pnl >= 0 ? '+' : ''}{formatUSD(c.pnl)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function LiveFromLeaders() {
  const { data: leaders, isLoading } = useQuery({
    queryKey: ['dashboard-followed-positions'],
    queryFn: () => getFollowedLeadersPositions(false),
    refetchInterval: 15000,
  });
  const [copyTarget, setCopyTarget] = useState<{ leader: FollowedLeader; position: FollowedLeaderPosition } | null>(null);

  if (isLoading) return <div className="flex justify-center py-6"><LoadingSpinner /></div>;

  if (!leaders || leaders.length === 0) {
    return (
      <div className="text-center py-6">
        <p className="text-sm text-text-secondary">You're not following anyone yet</p>
        <Link to="/copy" className="text-xs text-accent hover:text-accent/80 mt-1 inline-block">Browse Leaders</Link>
      </div>
    );
  }

  // Flatten to a list of {leader, position} pairs, take top 5
  const items: { leader: FollowedLeader; position: FollowedLeaderPosition }[] = [];
  for (const leader of leaders) {
    for (const position of leader.positions) {
      items.push({ leader, position });
      if (items.length >= 5) break;
    }
    if (items.length >= 5) break;
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-6">
        <p className="text-sm text-text-secondary">No open positions from followed traders</p>
        <Link to="/copy" className="text-xs text-accent hover:text-accent/80 mt-1 inline-block">View Leaderboard</Link>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-2">
        {items.map(({ leader, position }, i) => {
          const isLong = position.side === 'long';
          const pnl = position.pnl ?? 0;
          const deposit = position.deposit ?? 0;
          const pnlPct = deposit > 0 ? (pnl / deposit) * 100 : 0;
          return (
            <div key={`${leader.leader_wallet}-${position.market_id}-${i}`} className="flex items-center justify-between gap-3 p-3 rounded-lg bg-bg-primary/60">
              <div className="flex items-center gap-3 min-w-0">
                <span className={clsx(
                  'text-[10px] font-bold px-1.5 py-0.5 rounded shrink-0',
                  isLong ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger',
                )}>
                  {isLong ? 'LONG' : 'SHORT'}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-text-primary">{position.symbol}</span>
                    <span className="text-[10px] text-text-secondary">{position.leverage}x</span>
                  </div>
                  <div className="text-[10px] text-text-secondary truncate">
                    {leader.leader_display_name || shortenAddress(leader.leader_wallet)}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <div className={clsx('text-[10px] font-semibold', pnl >= 0 ? 'text-success' : 'text-danger')}>
                  {pnl >= 0 ? '+' : ''}{formatUSD(pnl)}
                  <span className="ml-1 text-text-secondary font-normal">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)</span>
                </div>
                <button
                  onClick={() => setCopyTarget({ leader, position })}
                  className={clsx(
                    'text-[10px] font-semibold px-2 py-1 rounded transition-colors',
                    isLong
                      ? 'bg-success/15 text-success hover:bg-success/25'
                      : 'bg-danger/15 text-danger hover:bg-danger/25',
                  )}
                >
                  Copy
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {copyTarget && (
        <CopyModal
          position={{
            market_id: copyTarget.position.market_id,
            symbol: copyTarget.position.symbol,
            side: copyTarget.position.side,
            entry_price: copyTarget.position.entry_price,
            mark_price: copyTarget.position.mark_price,
            leverage: copyTarget.position.leverage,
            size: copyTarget.position.size,
          }}
          copiedFrom={copyTarget.leader.leader_wallet}
          onClose={() => setCopyTarget(null)}
        />
      )}
    </>
  );
}

function FundingAlerts() {
  const { data, isLoading } = useQuery({
    queryKey: ['funding-comparison-dashboard'],
    queryFn: () => api.get('/api/funding-compare').then((r) => r.data),
    refetchInterval: 60000,
  });

  if (isLoading || !data) return null;

  // Show markets with notable funding signals (not neutral)
  const notable = (data.markets || []).filter((m: any) =>
    m.signal && m.signal !== 'neutral'
  );

  if (notable.length === 0) {
    return (
      <div className="text-center py-6">
        <p className="text-sm text-text-secondary">All funding rates neutral</p>
      </div>
    );
  }

  const signalColors: Record<string, string> = {
    strong_short: 'text-danger',
    short_bias: 'text-danger/70',
    strong_long: 'text-success',
    long_bias: 'text-success/70',
  };

  const signalLabels: Record<string, string> = {
    strong_short: 'STRONG SHORT',
    short_bias: 'SHORT BIAS',
    strong_long: 'STRONG LONG',
    long_bias: 'LONG BIAS',
  };

  return (
    <div className="space-y-2">
      {notable.slice(0, 4).map((m: any, i: number) => (
        <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-bg-primary/50">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-text-primary">{m.symbol}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-text-secondary">
              {m.perpl_rate !== undefined ? `${(m.perpl_rate * 100).toFixed(4)}%` : '--'}
            </span>
            <span className={clsx('text-[10px] font-bold', signalColors[m.signal] || 'text-text-secondary')}>
              {signalLabels[m.signal] || m.signal}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function ConnectWalletPrompt() {
  return (
    <div className="bg-gradient-to-br from-accent/5 to-bg-card border border-accent/10 rounded-xl p-6 text-center">
      <svg className="w-10 h-10 mx-auto mb-3 text-accent/40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
      </svg>
      <p className="text-sm font-medium text-text-primary">Connect wallet for personalized dashboard</p>
      <p className="text-xs text-text-secondary mt-1">See your positions, copy trades, and funding alerts</p>
    </div>
  );
}

function TradingQuickStats() {
  const { data: stats } = useQuery({
    queryKey: ['pnl-card-dashboard'],
    queryFn: getPnlCard,
    staleTime: 30000,
  });

  if (!stats || stats.total_trades === 0) return null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
      {[
        { label: 'Total PnL', value: (stats.total_pnl >= 0 ? '+' : '') + formatUSD(stats.total_pnl), color: stats.total_pnl >= 0 ? 'text-success' : 'text-danger' },
        { label: 'Win Rate', value: `${stats.win_rate}%`, color: stats.win_rate >= 50 ? 'text-success' : 'text-danger' },
        { label: 'Trades', value: stats.total_trades.toString(), color: 'text-text-primary' },
        { label: 'Today PnL', value: (stats.today_pnl >= 0 ? '+' : '') + formatUSD(stats.today_pnl), color: stats.today_pnl >= 0 ? 'text-success' : 'text-danger' },
        { label: 'Today Trades', value: stats.today_trades.toString(), color: 'text-text-primary' },
      ].map((s) => (
        <div key={s.label} className="bg-bg-secondary/50 border border-text-secondary/10 rounded-lg px-3 py-2.5">
          <div className="text-[9px] uppercase tracking-wider text-text-secondary">{s.label}</div>
          <div className={clsx('text-base font-bold mt-0.5', s.color)}>{s.value}</div>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const { markets, isLoading } = useMarketData();
  const { alerts } = useWhaleAlerts();
  const { isAuthenticated, address } = useAuth();
  const recentAlerts = alerts.slice(0, 5);

  const marketValues = Object.values(markets);
  const totalVolume = marketValues.reduce((sum, m) => sum + (m.daily_volume_usd ?? m.daily_volume * m.mark_price), 0);
  const totalOI = marketValues.reduce((sum, m) => sum + (m.open_interest_usd ?? m.open_interest * m.mark_price), 0);
  const totalTVL = marketValues.reduce((sum, m) => sum + (m.tvl ?? 0), 0);

  return (
    <div className="space-y-6 overflow-hidden">
      {/* Onboarding — only shows if not dismissed */}
      <OnboardingSection />

      {/* Hero header */}
      <div className="relative overflow-hidden rounded-xl bg-gradient-to-br from-accent/10 via-bg-secondary to-bg-primary border border-accent/10 p-4 sm:p-6">
        <div className="absolute top-0 right-0 w-64 h-64 bg-accent/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2" />
        <div className="relative">
          <h1 className="text-2xl sm:text-3xl font-bold text-text-primary tracking-tight">SMINDEX</h1>
          <p className="text-sm text-text-secondary mt-1 max-w-lg">
            Copy trading, liquidation heatmaps, and real-time analytics for Perpl perpetual futures on Monad
          </p>
          <div className="flex items-center gap-2 mt-3">
            <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
            <span className="text-xs text-text-secondary">{Object.keys(markets).length} markets live on Monad Mainnet</span>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <QuickActions />

      {/* Stats row */}
      {marketValues.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: '24h Volume', value: formatCompact(totalVolume), icon: (
              <svg className="w-5 h-5 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>
            )},
            { label: 'Open Interest', value: formatCompact(totalOI), icon: (
              <svg className="w-5 h-5 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
            )},
            { label: 'Total TVL', value: totalTVL > 0 ? formatCompact(totalTVL) : '--', icon: (
              <svg className="w-5 h-5 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            )},
            { label: 'Markets', value: marketValues.length.toString(), icon: (
              <svg className="w-5 h-5 text-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            )},
          ].map((stat) => (
            <div key={stat.label} className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl p-4 hover:border-text-secondary/20 transition-colors">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-bg-primary/50">{stat.icon}</div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-text-secondary">{stat.label}</div>
                  <div className="text-base sm:text-xl font-bold text-text-primary mt-0.5 truncate">{stat.value}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Your Trading Stats — quick glance */}
      {isAuthenticated && <TradingQuickStats />}

      {/* Live from followed traders — shown above the personal grid for prominence */}
      {isAuthenticated && address && (
        <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-accent/10">
                <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <h2 className="text-sm font-semibold text-text-primary">Live from Leaders</h2>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">Tap to copy</span>
            </div>
            <Link to="/copy" className="text-xs text-accent hover:text-accent/80 transition-colors">View All</Link>
          </div>
          <div className="p-4">
            <LiveFromLeaders />
          </div>
        </div>
      )}

      {/* Personalized sections for authenticated users */}
      {isAuthenticated && address ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Your Positions */}
          <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-accent/10">
                  <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                  </svg>
                </div>
                <h2 className="text-sm font-semibold text-text-primary">Your Positions</h2>
              </div>
              <Link to="/portfolio" className="text-xs text-accent hover:text-accent/80 transition-colors">View All</Link>
            </div>
            <div className="p-4">
              <UserPositions address={address} />
            </div>
          </div>

          {/* Recent Copy Trades */}
          <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-success/10">
                  <svg className="w-4 h-4 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                </div>
                <h2 className="text-sm font-semibold text-text-primary">Recent Copy Trades</h2>
              </div>
              <Link to="/copy" className="text-xs text-accent hover:text-accent/80 transition-colors">View All</Link>
            </div>
            <div className="p-4">
              <RecentCopyTrades address={address} />
            </div>
          </div>

          {/* Funding Alerts */}
          <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10">
              <div className="flex items-center gap-2">
                <div className="p-1.5 rounded-lg bg-warning/10">
                  <svg className="w-4 h-4 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
                <h2 className="text-sm font-semibold text-text-primary">Funding Signals</h2>
              </div>
              <Link to="/trade" className="text-xs text-accent hover:text-accent/80 transition-colors">Details</Link>
            </div>
            <div className="p-4">
              <FundingAlerts />
            </div>
          </div>
        </div>
      ) : (
        <ConnectWalletPrompt />
      )}

      {/* Market cards */}
      {isLoading ? (
        <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>
      ) : (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-text-primary">Markets</h2>
            <Link to="/trade" className="text-xs text-accent hover:text-accent/80 transition-colors">Open Terminal</Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {MARKET_IDS.map((id) => (
              <MarketTicker key={id} marketId={id} market={markets[id]} config={MARKETS[id]} />
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Whale Alerts */}
        <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-5 py-4 border-b border-text-secondary/10">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-danger/10">
                <svg className="w-4 h-4 text-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                </svg>
              </div>
              <h2 className="text-sm font-semibold text-text-primary">Whale Alerts</h2>
            </div>
            <Link to="/heatmap" className="text-xs text-accent hover:text-accent/80 transition-colors">View All</Link>
          </div>
          <div className="p-4">
            {recentAlerts.length === 0 ? (
              <div className="text-sm text-text-secondary text-center py-6">No recent whale activity</div>
            ) : (
              <div className="space-y-2">
                {recentAlerts.map((alert) => (
                  <WhaleAlertCard key={alert.id} alert={alert} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Quick Links */}
        <div className="bg-gradient-to-br from-bg-secondary to-bg-card border border-text-secondary/10 rounded-xl overflow-hidden">
          <div className="px-5 py-4 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Quick Access</h2>
          </div>
          <div className="p-4 space-y-2">
            {[
              { to: '/trade', label: 'Trading Terminal', desc: 'Chart, orderbook, and trade execution', icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6', bgClass: 'bg-accent/10 group-hover:bg-accent/20', textClass: 'text-accent' },
              { to: '/copy', label: 'Copy Trading', desc: 'Follow top traders and mirror positions', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z', bgClass: 'bg-success/10 group-hover:bg-success/20', textClass: 'text-success' },
              { to: '/heatmap', label: 'Liquidation Heatmap', desc: 'Visualize liquidation clusters', icon: 'M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z', bgClass: 'bg-danger/10 group-hover:bg-danger/20', textClass: 'text-danger' },
              { to: '/portfolio', label: 'Portfolio', desc: 'Positions, PnL, and risk metrics', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z', bgClass: 'bg-warning/10 group-hover:bg-warning/20', textClass: 'text-warning' },
            ].map((link) => (
              <Link
                key={link.to}
                to={link.to}
                className="flex items-center gap-3 p-3 rounded-lg hover:bg-bg-primary/50 transition-all group"
              >
                <div className={clsx('p-2 rounded-lg transition-colors', link.bgClass)}>
                  <svg className={clsx('w-4 h-4', link.textClass)} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={link.icon} />
                  </svg>
                </div>
                <div>
                  <div className="text-sm font-medium text-text-primary group-hover:text-accent transition-colors">{link.label}</div>
                  <div className="text-[10px] text-text-secondary">{link.desc}</div>
                </div>
                <svg className="w-4 h-4 text-text-secondary/30 ml-auto group-hover:text-accent/50 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
