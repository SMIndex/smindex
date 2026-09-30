import { clsx } from 'clsx';
import { formatUSD } from '@/lib/formatters';

interface PortfolioSummaryProps {
  balance: number;
  marginUsed: number;
  totalPnl: number;
  totalNotional: number;
  positionCount: number;
}

export default function PortfolioSummary({ balance, marginUsed, totalPnl, totalNotional, positionCount }: PortfolioSummaryProps) {
  const equity = balance + marginUsed + totalPnl;
  const marginPct = equity > 0 ? (marginUsed / equity) * 100 : 0;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {[
        { label: 'Equity', value: formatUSD(equity), color: '' },
        { label: 'Available', value: formatUSD(balance), color: '' },
        { label: 'Unrealized PnL', value: `${totalPnl >= 0 ? '+' : ''}${formatUSD(totalPnl)}`, color: totalPnl >= 0 ? 'text-success' : 'text-danger' },
        { label: 'Margin Used', value: formatUSD(marginUsed), color: '' },
        { label: 'Total Exposure', value: formatUSD(totalNotional), color: '' },
        { label: 'Positions', value: positionCount.toString(), color: '' },
      ].map((s) => (
        <div key={s.label} className="card py-3">
          <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">{s.label}</div>
          <div className={clsx('text-lg font-bold', s.color || 'text-text-primary')}>{s.value}</div>
        </div>
      ))}
    </div>
  );
}
