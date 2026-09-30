import { clsx } from 'clsx';
import type { PortfolioSummary } from '@/lib/copyPortfolio';
import { formatUSD } from '@/lib/formatters';

interface Props {
  summary: PortfolioSummary;
  subscribedAllocation: number;
}

function signedUSD(v: number): string {
  return `${v >= 0 ? '+' : ''}${formatUSD(v)}`;
}

interface CardProps {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'neutral';
  hint?: string;
  estimated?: boolean;
}

function Card({ label, value, tone = 'neutral', hint, estimated }: CardProps) {
  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4 flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-medium text-text-secondary/70 uppercase tracking-wide">{label}</span>
        {estimated && (
          <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-accent/10 text-accent">EST</span>
        )}
      </div>
      <span className={clsx(
        'rd-mono text-[20px] font-bold tabular-nums',
        tone === 'pos' && 'text-success',
        tone === 'neg' && 'text-danger',
        tone === 'neutral' && 'text-text-primary',
      )}>
        {value}
      </span>
      {hint && <span className="text-[10px] text-text-secondary/50">{hint}</span>}
    </div>
  );
}

export default function PortfolioSummaryCards({ summary, subscribedAllocation }: Props) {
  const tone = (v: number): 'pos' | 'neg' | 'neutral' => (v > 0 ? 'pos' : v < 0 ? 'neg' : 'neutral');
  const netPartial = summary.unrealizedIncomplete || summary.feesIncomplete;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
      <Card label="Est. Net PnL" value={signedUSD(summary.netEst)} tone={tone(summary.netEst)} estimated
        hint={netPartial ? 'partial — some live marks/fees unavailable' : 'realized + unrealized − est. fees'} />
      <Card label="Realized PnL" value={signedUSD(summary.realized)} tone={tone(summary.realized)} />
      <Card label="Unrealized PnL" value={signedUSD(summary.unrealized)} tone={tone(summary.unrealized)}
        hint={summary.unrealizedIncomplete ? 'some marks unavailable' : undefined} />
      <Card label="Allocation" value={formatUSD(summary.allocationOpen)}
        hint={`${formatUSD(subscribedAllocation)} subscribed`} />
      <Card label="Open Positions" value={String(summary.openCount)}
        hint={`${summary.closedCount} closed`} />
      <Card label="Win Rate" value={summary.winRate == null ? '—' : `${summary.winRate.toFixed(0)}%`}
        hint={summary.closedCount ? `${summary.closedCount} closed` : 'no closed trades'} />
      <Card label="Est. Fees" value={formatUSD(summary.estFees)} estimated hint="open-side taker · close 0" />
    </div>
  );
}
