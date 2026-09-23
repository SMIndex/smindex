import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api, { getCopyPerformance } from '@/lib/api';
import { useCopyStore } from '@/stores/copyStore';
import { shortenAddress, formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { PortfolioSkeleton, SkeletonChart } from '@/components/common/Skeleton';
import { createChart, ColorType, AreaSeries } from 'lightweight-charts';
import PnLCalendar from '@/components/portfolio/PnLCalendar';

const RISK_COLORS: Record<string, string> = {
  safe: 'text-success bg-success/10',
  warning: 'text-warning bg-warning/10',
  danger: 'text-danger bg-danger/10',
  critical: 'text-red-500 bg-red-500/10 animate-pulse',
};
const RISK_LABELS: Record<string, string> = {
  safe: 'Low Risk',
  warning: 'Medium Risk',
  danger: 'High Risk',
  critical: 'CRITICAL',
};

type Tab = 'overview' | 'health' | 'equity' | 'calendar' | 'history' | 'copy_perf';

// --- Equity Curve Chart Component ---
function EquityCurve({ wallet }: { wallet: string }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);
  const [days, setDays] = useState(30);

  const { data: curveData, isLoading } = useQuery({
    queryKey: ['equity-curve', wallet, days],
    queryFn: () => api.get(`/api/account-health/equity-curve?days=${days}`).then((r) => r.data),
    enabled: !!wallet,
    refetchInterval: 60000,
  });

  // Force initial snapshot if no data
  const { data: snapshotDone } = useQuery({
    queryKey: ['equity-snapshot-init', wallet],
    queryFn: () => api.post('/api/account-health/equity-curve/snapshot').then((r) => r.data),
    enabled: !!wallet && curveData !== undefined && curveData.length === 0,
    retry: false,
  });

  useEffect(() => {
    if (!chartRef.current || !curveData || curveData.length === 0) return;

    // Clear previous chart
    if (chartInstanceRef.current) {
      chartInstanceRef.current.remove();
      chartInstanceRef.current = null;
    }

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 220,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#6b7280',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(107, 114, 128, 0.1)' },
        horzLines: { color: 'rgba(107, 114, 128, 0.1)' },
      },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: {
        horzLine: { visible: true, labelVisible: true },
        vertLine: { visible: true, labelVisible: true },
      },
    });

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: '#6366f1',
      topColor: 'rgba(99, 102, 241, 0.3)',
      bottomColor: 'rgba(99, 102, 241, 0.02)',
      lineWidth: 2,
      priceFormat: { type: 'custom', formatter: (p: number) => `$${p.toFixed(2)}` },
    });

    const chartData = curveData.map((s: any) => ({
      time: Math.floor(new Date(s.timestamp).getTime() / 1000),
      value: s.equity,
    }));

    areaSeries.setData(chartData);
    chart.timeScale().fitContent();
    chartInstanceRef.current = chart;

    const handleResize = () => {
      if (chartRef.current) chart.applyOptions({ width: chartRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartInstanceRef.current = null;
    };
  }, [curveData]);

  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Equity Curve</h2>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={clsx('text-[10px] px-2 py-0.5 rounded', days === d ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary hover:text-text-primary')}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <div className="p-4"><SkeletonChart /></div>
      ) : !curveData || curveData.length === 0 ? (
        <div className="text-center py-12 text-xs text-text-secondary">
          No equity data yet. Snapshots are taken every 4 hours when you have open positions.
          {snapshotDone && <div className="mt-1 text-accent">First snapshot recorded — check back later for the curve.</div>}
        </div>
      ) : (
        <div ref={chartRef} className="w-full" />
      )}
    </div>
  );
}

export default function PortfolioPage() {
  const { address, hydrated } = useAuth();
  const copies = useCopyStore((s) => s.copies);
  const [tab, setTab] = useState<Tab>('overview');

  // Health dashboard data (includes positions, account metrics, risk)
  const { data: healthData, isLoading } = useQuery({
    queryKey: ['account-health', address],
    queryFn: () => api.get('/api/account-health').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 5000,
  });

  if (!hydrated) {
    return <div className="flex justify-center py-12"><div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" /></div>;
  }

  if (!address) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Portfolio</h1>
        <div className="card text-center py-12 text-sm text-text-secondary">Connect your wallet to view portfolio</div>
      </div>
    );
  }

  if (isLoading) {
    return <PortfolioSkeleton />;
  }

  // If no on-chain account, show a simplified portfolio with available data
  const hasOnChainAccount = healthData?.connected;
  const account = hasOnChainAccount ? healthData.account : {
    equity: 0, balance: 0, available: 0, margin_used: 0,
    margin_ratio_pct: 0, account_leverage: 0,
    total_unrealized_pnl: 0, total_notional: 0, position_count: 0,
  };
  const risk = hasOnChainAccount ? healthData.risk : {
    level: 'safe', closest_liq_pct: null, closest_liq_market: null,
    positions_with_sl: 0, positions_with_tp: 0, unprotected_positions: 0,
  };
  const positions = hasOnChainAccount ? healthData.positions : [];

  return (
    <div className="space-y-4">
      {/* No on-chain account notice */}
      {!hasOnChainAccount && (
        <div className="card border-warning/20 bg-warning/5">
          <p className="text-sm text-text-primary">No Perpl exchange account found for this wallet.</p>
          <p className="text-xs text-text-secondary mt-1">Visit <a href="https://perpl.xyz" target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">perpl.xyz</a> to create an account and deposit funds. Your copy trade history and other data are still shown below.</p>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Portfolio</h1>
          <p className="text-xs text-text-secondary font-mono">{shortenAddress(address)}</p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <button
            onClick={() => {
              api.get('/api/export/pnl', { responseType: 'blob' }).then((res) => {
                const url = window.URL.createObjectURL(new Blob([res.data]));
                const a = document.createElement('a');
                a.href = url;
                a.download = `pnl_${address?.slice(0, 10)}_${new Date().toISOString().slice(0, 10)}.csv`;
                a.click();
                window.URL.revokeObjectURL(url);
              });
            }}
            className="text-xs px-3 py-1.5 rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-colors font-medium"
          >
            Export PnL
          </button>
          <div className={clsx('px-3 py-1.5 rounded-lg font-bold text-xs', RISK_COLORS[risk.level])}>
            {RISK_LABELS[risk.level]}
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
            <span className="text-[10px] text-text-secondary">Live</span>
          </div>
        </div>
      </div>

      {/* Account Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          { label: 'Equity', value: formatUSD(account.equity), color: '' },
          { label: 'Available', value: formatUSD(account.available), color: '' },
          { label: 'Unrealized PnL', value: `${account.total_unrealized_pnl >= 0 ? '+' : ''}${formatUSD(account.total_unrealized_pnl)}`, color: account.total_unrealized_pnl >= 0 ? 'text-success' : 'text-danger' },
          { label: 'Margin Used', value: formatUSD(account.margin_used), color: '' },
          { label: 'Leverage', value: `${account.account_leverage.toFixed(1)}x`, color: '' },
          { label: 'Exposure', value: formatUSD(account.total_notional), color: '' },
        ].map((s) => (
          <div key={s.label} className="card py-3">
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">{s.label}</div>
            <div className={clsx('text-base sm:text-lg font-bold truncate', s.color || 'text-text-primary')}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Risk Meters Row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {/* Margin Usage */}
        <div className="card py-3 space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary">Margin Usage</div>
          <div className="text-lg font-bold text-text-primary">{account.margin_ratio_pct.toFixed(1)}%</div>
          <div className="w-full h-1.5 bg-bg-secondary rounded-full">
            <div
              className={clsx('h-full rounded-full', account.margin_ratio_pct > 80 ? 'bg-danger' : account.margin_ratio_pct > 50 ? 'bg-warning' : 'bg-success')}
              style={{ width: `${Math.min(account.margin_ratio_pct, 100)}%` }}
            />
          </div>
        </div>

        {/* Closest Liquidation */}
        <div className="card py-3 space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary">Closest Liquidation</div>
          {risk.closest_liq_pct !== null ? (
            <>
              <div className={clsx('text-lg font-bold', risk.closest_liq_pct < 10 ? 'text-danger' : risk.closest_liq_pct < 20 ? 'text-warning' : 'text-success')}>
                {risk.closest_liq_pct.toFixed(1)}% away
              </div>
              <div className="text-[10px] text-text-secondary">{risk.closest_liq_market} position</div>
            </>
          ) : (
            <div className="text-lg font-bold text-text-secondary">--</div>
          )}
        </div>

        {/* SL/TP Coverage */}
        <div className="card py-3 space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary">SL/TP Protection</div>
          <div className="flex items-end gap-3 sm:gap-4">
            <div>
              <div className="text-[10px] text-text-secondary">Stop Loss</div>
              <div className={clsx('text-base sm:text-lg font-bold', risk.positions_with_sl === account.position_count && account.position_count > 0 ? 'text-success' : risk.unprotected_positions > 0 ? 'text-warning' : 'text-text-primary')}>
                {risk.positions_with_sl}/{account.position_count}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">Take Profit</div>
              <div className="text-base sm:text-lg font-bold text-text-primary">{risk.positions_with_tp}/{account.position_count}</div>
            </div>
          </div>
          {risk.unprotected_positions > 0 && (
            <div className="text-[10px] text-warning">{risk.unprotected_positions} unprotected</div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-bg-secondary rounded-lg p-1 overflow-x-auto scrollbar-hide">
        {([
          ['overview', `Positions (${positions.length})`],
          ['health', 'Position Health'],
          ['equity', 'Equity Curve'],
          ['calendar', 'PnL Calendar'],
          ['history', 'Copy History'],
          ['copy_perf', 'Copy Performance'],
        ] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={clsx(
              'px-3 sm:px-4 py-2 rounded-md text-xs sm:text-sm font-medium transition-colors whitespace-nowrap shrink-0',
              tab === key ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {tab === 'overview' && (
        <div className="card p-0 overflow-hidden">
          {positions.length === 0 ? (
            <div className="text-center py-8 text-sm text-text-secondary">No open positions</div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-secondary border-b border-text-secondary/10">
                      <th className="px-4 py-2 text-left font-medium">Market</th>
                      <th className="px-2 py-2 text-left font-medium">Size</th>
                      <th className="px-2 py-2 text-left font-medium">Entry</th>
                      <th className="px-2 py-2 text-left font-medium">Mark</th>
                      <th className="px-2 py-2 text-right font-medium">PnL</th>
                      <th className="px-2 py-2 text-right font-medium">ROE</th>
                      <th className="px-2 py-2 text-right font-medium">Margin</th>
                      <th className="px-2 py-2 text-right font-medium">Liq. Price</th>
                      <th className="px-3 py-2 text-center font-medium">SL/TP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((pos: any) => {
                      const cfg = MARKETS[pos.market_id];
                      const dec = cfg?.decimals ?? 2;
                      return (
                        <tr key={pos.market_id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                          <td className="px-4 py-2.5">
                            <span className="font-bold text-text-primary">{pos.symbol}</span>
                            <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                              {pos.side} {pos.leverage.toFixed(0)}x
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-text-primary">{pos.size.toFixed(cfg?.sizeDecimals ?? 4)}</td>
                          <td className="px-2 py-2.5 text-text-primary">${formatPrice(pos.entry_price, dec)}</td>
                          <td className="px-2 py-2.5 text-text-primary">${formatPrice(pos.mark_price, dec)}</td>
                          <td className="px-2 py-2.5 text-right">
                            <span className={clsx('font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                              {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-right">
                            <span className={clsx('font-medium', pos.roe_pct >= 0 ? 'text-success' : 'text-danger')}>
                              {pos.roe_pct >= 0 ? '+' : ''}{pos.roe_pct.toFixed(1)}%
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-right text-text-primary">{formatUSD(pos.deposit)}</td>
                          <td className="px-2 py-2.5 text-right text-danger">${formatPrice(pos.liq_price, dec)}</td>
                          <td className="px-3 py-2.5 text-center">
                            <div className="flex justify-center gap-1">
                              <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                              <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                            </div>
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
                  return (
                    <div key={pos.market_id} className="px-3 py-2 border-t border-text-secondary/5 text-[11px]">
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="font-bold text-text-primary">{pos.symbol}</span>
                          <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                            {pos.side} {pos.leverage.toFixed(0)}x
                          </span>
                        </div>
                        <div className="text-right">
                          <span className={clsx('font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                            {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                          </span>
                          <span className={clsx('ml-1.5 text-[10px]', pos.roe_pct >= 0 ? 'text-success' : 'text-danger')}>
                            {pos.roe_pct >= 0 ? '+' : ''}{pos.roe_pct.toFixed(1)}%
                          </span>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                        <span>Entry: ${formatPrice(pos.entry_price, dec)}</span>
                        <span>Mark: ${formatPrice(pos.mark_price, dec)}</span>
                        <span>Margin: {formatUSD(pos.deposit)}</span>
                        <span className="text-danger">Liq: ${formatPrice(pos.liq_price, dec)}</span>
                        <span>Size: {pos.size.toFixed(cfg?.sizeDecimals ?? 4)}</span>
                        <div className="flex gap-1">
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'health' && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Position Health</h2>
          </div>
          {positions.length === 0 ? (
            <div className="text-center py-8 text-sm text-text-secondary">No open positions</div>
          ) : (
            <div className="space-y-0">
              {positions.map((pos: any) => {
                const cfg = MARKETS[pos.market_id];
                const dec = cfg?.decimals ?? 2;
                return (
                  <div key={pos.market_id} className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 px-4 py-3 border-b border-text-secondary/5 last:border-0">
                    <div className="flex items-center justify-between sm:justify-start sm:w-20">
                      <div>
                        <span className="font-bold text-text-primary text-xs">{pos.symbol}</span>
                        <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                          {pos.side}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 sm:hidden">
                        <div className="flex gap-1">
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                        </div>
                        <span className={clsx('text-xs font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                        </span>
                      </div>
                    </div>
                    <div className="flex-1">
                      <div className="flex justify-between text-[10px] mb-1">
                        <span className="text-text-secondary">Liq: ${formatPrice(pos.liq_price, dec)} ({pos.liq_distance_pct.toFixed(1)}% away)</span>
                        <span className={clsx('font-bold', pos.health_score > 60 ? 'text-success' : pos.health_score > 30 ? 'text-warning' : 'text-danger')}>
                          Health: {pos.health_score}/100
                        </span>
                      </div>
                      <div className="w-full h-2.5 bg-bg-secondary rounded-full">
                        <div
                          className={clsx('h-full rounded-full transition-all', pos.health_score > 60 ? 'bg-success' : pos.health_score > 30 ? 'bg-warning' : 'bg-danger')}
                          style={{ width: `${pos.health_score}%` }}
                        />
                      </div>
                    </div>
                    <div className="hidden sm:flex gap-1 w-14 justify-end">
                      <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                      <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                    </div>
                    <div className="hidden sm:block w-20 text-right">
                      <span className={clsx('text-xs font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                        {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {tab === 'equity' && <EquityCurve wallet={address} />}

      {tab === 'calendar' && <PnLCalendar />}

      {tab === 'history' && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Copy Trade History</h2>
          </div>
          {copies.length === 0 ? (
            <div className="text-center py-8 text-sm text-text-secondary">No copy trades recorded</div>
          ) : (
            <div className="divide-y divide-text-secondary/5">
              {copies.map((c, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2 sm:gap-4 px-4 py-3 text-xs">
                  <span className="font-bold text-text-primary">{c.symbol}</span>
                  <span className={clsx('font-semibold uppercase px-1.5 py-0.5 rounded text-[10px]', c.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                    {c.side} {c.leverage}x
                  </span>
                  <span className="text-text-secondary">{formatUSD(c.amount_usd)} margin</span>
                  <span className="text-accent text-[10px]">from {shortenAddress(c.copied_from)}</span>
                  <span className="text-text-secondary/50 text-[10px] ml-auto">{new Date(c.timestamp).toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === 'copy_perf' && <CopyPerformanceTab />}
    </div>
  );
}

function CopyPerformanceTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['copy-performance'],
    queryFn: getCopyPerformance,
  });

  if (isLoading) return <div className="flex justify-center py-8"><LoadingSpinner /></div>;
  if (!data || data.length === 0) return <div className="card text-center py-8 text-sm text-text-secondary">No copy trades yet</div>;

  return (
    <div className="space-y-3">
      {data.map((leader: any) => (
        <div key={leader.leader_wallet} className="card p-0 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-text-secondary/10">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center">
                <span className="text-[10px] font-bold text-accent">{leader.leader_wallet[2]?.toUpperCase()}</span>
              </div>
              <div>
                <span className="text-sm font-mono font-medium text-text-primary">{shortenAddress(leader.leader_wallet)}</span>
                <div className="text-[10px] text-text-secondary">{leader.total_copies} copies | {leader.closed_copies} closed | {leader.open_copies} open</div>
              </div>
            </div>
            <div className="text-right">
              <div className={clsx('text-lg font-bold', leader.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                {leader.total_pnl >= 0 ? '+' : ''}{formatUSD(leader.total_pnl)}
              </div>
              <div className="text-[10px] text-text-secondary">
                {leader.win_rate}% win | {formatUSD(leader.total_volume)} vol
              </div>
            </div>
          </div>
          {leader.trades.length > 0 && (
            <div className="divide-y divide-text-secondary/5">
              {leader.trades.map((t: any, i: number) => (
                <div key={i} className="flex items-center justify-between px-4 py-2 text-[11px]">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-text-primary">{t.symbol}</span>
                    <span className={clsx('font-semibold uppercase text-[10px]', t.side === 'long' ? 'text-success' : 'text-danger')}>{t.side}</span>
                    <span className="text-text-secondary">${formatPrice(t.entry_price, 6)} → ${formatPrice(t.close_price, 6)}</span>
                  </div>
                  <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                    {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
