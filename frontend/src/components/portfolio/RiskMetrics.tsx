import { clsx } from 'clsx';
import { useMarketStore } from '@/stores/marketStore';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';

interface RiskMetricsProps {
  positions: any[];
  balance: number;
  marginUsed: number;
}

export default function RiskMetrics({ positions, balance, marginUsed }: RiskMetricsProps) {
  const markets = useMarketStore((s) => s.markets);

  const equity = balance + marginUsed;
  const marginPct = equity > 0 ? (marginUsed / equity) * 100 : 0;
  const totalNotional = positions.reduce((s: number, p: any) => s + (p.notional || 0), 0);

  // Calculate liq distances
  const liqDistances = positions.map((pos: any) => {
    const mcfg = MARKET_CONFIGS[pos.market_id];
    const mmrFrac = 100 / (mcfg?.maintenanceMarginHdths ?? 2000);
    const mmr = pos.notional * mmrFrac;
    const liqPrice = pos.side === 'long'
      ? pos.entry_price - (pos.deposit - mmr) / pos.size
      : pos.entry_price + (pos.deposit - mmr) / pos.size;
    const m = markets[pos.market_id];
    const markPrice = m?.mark_price ?? pos.mark_price;
    const dist = markPrice > 0 ? Math.abs(liqPrice - markPrice) / markPrice * 100 : 100;
    return { symbol: pos.symbol, side: pos.side, dist, liqPrice, markPrice, marketId: pos.market_id };
  });

  const closestLiq = liqDistances.length > 0
    ? liqDistances.reduce((min, d) => d.dist < min.dist ? d : min)
    : null;

  const riskLevel = !closestLiq ? 'safe' : closestLiq.dist < 10 ? 'danger' : closestLiq.dist < 20 ? 'warning' : 'safe';
  const riskColors = { safe: 'text-success bg-success/10', warning: 'text-warning bg-warning/10', danger: 'text-danger bg-danger/10' };
  const riskLabels = { safe: 'Low Risk', warning: 'Medium Risk', danger: 'High Risk' };

  return (
    <div className="space-y-4">
      {/* Risk gauge */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Risk Level</div>
          <div className={clsx('text-sm font-bold px-2 py-1 rounded inline-block', riskColors[riskLevel])}>
            {riskLabels[riskLevel]}
          </div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Margin Usage</div>
          <div className="text-lg font-bold text-text-primary">{marginPct.toFixed(1)}%</div>
          <div className="w-full h-1.5 bg-bg-secondary rounded-full mt-1">
            <div className={clsx('h-full rounded-full', marginPct > 80 ? 'bg-danger' : marginPct > 50 ? 'bg-warning' : 'bg-success')} style={{ width: `${Math.min(marginPct, 100)}%` }} />
          </div>
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Closest Liquidation</div>
          {closestLiq ? (
            <div>
              <div className={clsx('text-lg font-bold', closestLiq.dist < 10 ? 'text-danger' : closestLiq.dist < 20 ? 'text-warning' : 'text-success')}>
                {closestLiq.dist.toFixed(1)}% away
              </div>
              <div className="text-[10px] text-text-secondary">
                {closestLiq.symbol} {closestLiq.side} @ ${formatPrice(closestLiq.liqPrice, MARKETS[closestLiq.marketId]?.decimals ?? 2)}
              </div>
            </div>
          ) : (
            <div className="text-lg font-bold text-text-secondary">--</div>
          )}
        </div>
        <div className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Leverage Exposure</div>
          <div className="text-lg font-bold text-text-primary">
            {equity > 0 ? `${(totalNotional / equity).toFixed(1)}x` : '--'}
          </div>
        </div>
      </div>

      {/* Per-position risk */}
      {liqDistances.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-2 border-b border-text-secondary/10 text-xs font-medium text-text-secondary">Liquidation Distance by Position</div>
          {liqDistances.map((d, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-2 text-xs border-b border-text-secondary/5 last:border-0">
              <span className="font-bold text-text-primary w-12">{d.symbol}</span>
              <span className={clsx('w-12 font-medium uppercase', d.side === 'long' ? 'text-success' : 'text-danger')}>{d.side}</span>
              <div className="flex-1">
                <div className="w-full h-2 bg-bg-secondary rounded-full">
                  <div
                    className={clsx('h-full rounded-full', d.dist < 10 ? 'bg-danger' : d.dist < 20 ? 'bg-warning' : 'bg-success')}
                    style={{ width: `${Math.min(d.dist, 50) * 2}%` }}
                  />
                </div>
              </div>
              <span className={clsx('w-16 text-right font-bold', d.dist < 10 ? 'text-danger' : d.dist < 20 ? 'text-warning' : 'text-success')}>
                {d.dist.toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
