import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import LoadingSpinner from '@/components/common/LoadingSpinner';

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

function HealthGauge({ value, max = 100, label, color }: { value: number; max?: number; label: string; color: string }) {
  const pct = Math.min(value / max * 100, 100);
  return (
    <div>
      <div className="flex justify-between text-[10px] mb-1">
        <span className="text-text-secondary">{label}</span>
        <span className={clsx('font-bold', color)}>{value.toFixed(1)}%</span>
      </div>
      <div className="w-full h-2 bg-bg-secondary rounded-full">
        <div className={clsx('h-full rounded-full transition-all', color.replace('text-', 'bg-'))} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function HealthPage() {
  const { address, hydrated } = useAuth();

  const { data, isLoading, error } = useQuery({
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
        <h1 className="text-xl font-bold text-text-primary">Account Health</h1>
        <div className="card text-center py-12 text-sm text-text-secondary">Connect wallet to view account health</div>
      </div>
    );
  }

  if (isLoading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>;

  if (!data?.connected) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Account Health</h1>
        <div className="card text-center py-12 text-sm text-text-secondary">{data?.message || 'No Perpl account found'}</div>
      </div>
    );
  }

  const { account, risk, positions } = data;

  return (
    <div className="space-y-4 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-text-primary">Account Health</h1>
          <p className="text-xs text-text-secondary">Real-time risk monitoring</p>
        </div>
        <div className={clsx('px-3 py-1.5 rounded-lg font-bold text-sm', RISK_COLORS[risk.level])}>
          {RISK_LABELS[risk.level]}
        </div>
      </div>

      {/* Account Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Equity</div>
          <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(account.equity)}</div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Available</div>
          <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(account.available)}</div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Unrealized PnL</div>
          <div className={clsx('text-base sm:text-lg font-bold truncate', account.total_unrealized_pnl >= 0 ? 'text-success' : 'text-danger')}>
            {account.total_unrealized_pnl >= 0 ? '+' : ''}{formatUSD(account.total_unrealized_pnl)}
          </div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Margin Used</div>
          <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(account.margin_used)}</div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Account Leverage</div>
          <div className="text-base sm:text-lg font-bold text-text-primary">{account.account_leverage.toFixed(1)}x</div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Exposure</div>
          <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(account.total_notional)}</div>
        </div>
      </div>

      {/* Risk Meters */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="card py-4 space-y-3">
          <div className="text-xs font-semibold text-text-primary">Margin Usage</div>
          <HealthGauge
            value={account.margin_ratio_pct}
            label="Used / Equity"
            color={account.margin_ratio_pct > 80 ? 'text-danger' : account.margin_ratio_pct > 50 ? 'text-warning' : 'text-success'}
          />
        </div>
        <div className="card py-4 space-y-3">
          <div className="text-xs font-semibold text-text-primary">Closest Liquidation</div>
          {risk.closest_liq_pct !== null ? (
            <>
              <div className={clsx('text-2xl font-bold', risk.closest_liq_pct < 10 ? 'text-danger' : risk.closest_liq_pct < 20 ? 'text-warning' : 'text-success')}>
                {risk.closest_liq_pct.toFixed(1)}%
              </div>
              <div className="text-[10px] text-text-secondary">{risk.closest_liq_market} position</div>
            </>
          ) : (
            <div className="text-2xl font-bold text-text-secondary">--</div>
          )}
        </div>
        <div className="card py-4 space-y-3">
          <div className="text-xs font-semibold text-text-primary">SL/TP Coverage</div>
          <div className="flex items-end gap-3">
            <div>
              <div className="text-[10px] text-text-secondary">Stop Loss</div>
              <div className={clsx('text-lg font-bold', risk.positions_with_sl === account.position_count ? 'text-success' : risk.unprotected_positions > 0 ? 'text-warning' : 'text-text-primary')}>
                {risk.positions_with_sl}/{account.position_count}
              </div>
            </div>
            <div>
              <div className="text-[10px] text-text-secondary">Take Profit</div>
              <div className="text-lg font-bold text-text-primary">{risk.positions_with_tp}/{account.position_count}</div>
            </div>
          </div>
          {risk.unprotected_positions > 0 && (
            <div className="text-[10px] text-warning">{risk.unprotected_positions} position(s) without stop loss</div>
          )}
        </div>
      </div>

      {/* Per-Position Health */}
      {positions.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Position Health</h2>
          </div>
          {/* Desktop table */}
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-text-secondary border-b border-text-secondary/10">
                  <th className="px-4 py-2 text-left font-medium">Market</th>
                  <th className="px-3 py-2 text-left font-medium">Side</th>
                  <th className="px-3 py-2 text-right font-medium">Size</th>
                  <th className="px-3 py-2 text-right font-medium">Entry</th>
                  <th className="px-3 py-2 text-right font-medium">Mark</th>
                  <th className="px-3 py-2 text-right font-medium">PnL</th>
                  <th className="px-3 py-2 text-right font-medium">ROE</th>
                  <th className="px-3 py-2 text-right font-medium">Liq. Price</th>
                  <th className="px-3 py-2 text-right font-medium">Liq. Dist</th>
                  <th className="px-3 py-2 text-center font-medium">SL/TP</th>
                  <th className="px-4 py-2 text-right font-medium">Health</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos: any) => {
                  const cfg = MARKETS[pos.market_id];
                  const dec = cfg?.decimals ?? 2;
                  return (
                    <tr key={pos.market_id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                      <td className="px-4 py-2.5 font-bold text-text-primary">{pos.symbol}</td>
                      <td className="px-3 py-2.5">
                        <span className={clsx('font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded', pos.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                          {pos.side} {pos.leverage.toFixed(0)}x
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-text-primary">{pos.size.toFixed(cfg?.sizeDecimals ?? 4)}</td>
                      <td className="px-3 py-2.5 text-right text-text-primary">${formatPrice(pos.entry_price, dec)}</td>
                      <td className="px-3 py-2.5 text-right text-text-primary">${formatPrice(pos.mark_price, dec)}</td>
                      <td className="px-3 py-2.5 text-right">
                        <span className={clsx('font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <span className={clsx('font-bold', pos.roe_pct >= 0 ? 'text-success' : 'text-danger')}>
                          {pos.roe_pct >= 0 ? '+' : ''}{pos.roe_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-danger">${formatPrice(pos.liq_price, dec)}</td>
                      <td className="px-3 py-2.5 text-right">
                        <span className={clsx('font-bold', pos.liq_distance_pct < 10 ? 'text-danger' : pos.liq_distance_pct < 20 ? 'text-warning' : 'text-success')}>
                          {pos.liq_distance_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <div className="flex justify-center gap-1">
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                          <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <div className="flex items-center justify-end gap-2">
                          <div className="w-12 h-2 bg-bg-secondary rounded-full">
                            <div
                              className={clsx('h-full rounded-full', pos.health_score > 60 ? 'bg-success' : pos.health_score > 30 ? 'bg-warning' : 'bg-danger')}
                              style={{ width: `${pos.health_score}%` }}
                            />
                          </div>
                          <span className={clsx('text-[10px] font-bold w-6 text-right', pos.health_score > 60 ? 'text-success' : pos.health_score > 30 ? 'text-warning' : 'text-danger')}>
                            {pos.health_score}
                          </span>
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
                    <div className="flex items-center gap-1.5">
                      <span className="font-bold text-text-primary">{pos.symbol}</span>
                      <span className={clsx('font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded', pos.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                        {pos.side} {pos.leverage.toFixed(0)}x
                      </span>
                      <div className="flex gap-1">
                        <span className={clsx('text-[9px] px-1 rounded', pos.has_sl ? 'bg-danger/10 text-danger' : 'bg-bg-secondary text-text-secondary/30')}>SL</span>
                        <span className={clsx('text-[9px] px-1 rounded', pos.has_tp ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary/30')}>TP</span>
                      </div>
                    </div>
                    <span className={clsx('font-bold', pos.total_pnl >= 0 ? 'text-success' : 'text-danger')}>
                      {pos.total_pnl >= 0 ? '+' : ''}{formatUSD(pos.total_pnl)}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                    <span>Entry: ${formatPrice(pos.entry_price, dec)}</span>
                    <span>Mark: ${formatPrice(pos.mark_price, dec)}</span>
                    <span className="text-danger">Liq: ${formatPrice(pos.liq_price, dec)} ({pos.liq_distance_pct.toFixed(1)}%)</span>
                    <span>ROE: <span className={clsx(pos.roe_pct >= 0 ? 'text-success' : 'text-danger')}>{pos.roe_pct >= 0 ? '+' : ''}{pos.roe_pct.toFixed(1)}%</span></span>
                  </div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <div className="flex-1 h-2 bg-bg-secondary rounded-full">
                      <div
                        className={clsx('h-full rounded-full', pos.health_score > 60 ? 'bg-success' : pos.health_score > 30 ? 'bg-warning' : 'bg-danger')}
                        style={{ width: `${pos.health_score}%` }}
                      />
                    </div>
                    <span className={clsx('text-[10px] font-bold', pos.health_score > 60 ? 'text-success' : pos.health_score > 30 ? 'text-warning' : 'text-danger')}>
                      {pos.health_score}/100
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {positions.length === 0 && (
        <div className="card text-center py-8 text-sm text-text-secondary">No open positions</div>
      )}
    </div>
  );
}
