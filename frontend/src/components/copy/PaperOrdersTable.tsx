import { clsx } from 'clsx';
import type { CopyOrder } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatSize, formatTimeAgo, shortenAddress } from '@/lib/formatters';

interface Props {
  orders: CopyOrder[];
  loading?: boolean;
}

const STATUS_STYLE: Record<string, string> = {
  filled: 'text-success bg-success/10',
  simulated: 'text-accent bg-accent/10',
  skipped: 'text-warning bg-warning/10',
  rejected: 'text-danger bg-danger/10',
};

export default function PaperOrdersTable({ orders, loading }: Props) {
  if (loading) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">Loading paper orders…</div>;
  }
  if (!orders.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No paper orders yet. The copy decision log fills as copied traders trade.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
            <th className="py-2 px-2 font-medium">Time</th>
            <th className="py-2 px-2 font-medium">Market</th>
            <th className="py-2 px-2 font-medium">Side</th>
            <th className="py-2 px-2 font-medium text-right">Intended Size</th>
            <th className="py-2 px-2 font-medium text-right">Intended Price</th>
            <th className="py-2 px-2 font-medium">Status</th>
            <th className="py-2 px-2 font-medium">Reason</th>
            <th className="py-2 px-2 font-medium">Trader</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => {
            const decimals = MARKETS[o.market_id]?.decimals ?? 2;
            const skipped = o.status === 'skipped' || o.status === 'rejected';
            return (
              <tr key={o.id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="py-2.5 px-2 text-text-secondary/70 whitespace-nowrap">{o.created_at ? formatTimeAgo(o.created_at) : '—'}</td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{o.symbol || MARKETS[o.market_id]?.symbol || o.market_id}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('font-medium', /long|buy|open/i.test(o.side) ? 'text-success' : 'text-danger')}>
                    {o.side?.toUpperCase()}
                  </span>
                </td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-primary">{o.intended_size != null ? formatSize(o.intended_size) : '—'}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{o.intended_price != null ? formatPrice(o.intended_price, decimals) : '—'}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded uppercase', STATUS_STYLE[o.status] || 'text-text-secondary bg-text-secondary/10')}>
                    {o.status}
                  </span>
                </td>
                <td className={clsx('py-2.5 px-2', skipped ? 'text-warning' : 'text-text-secondary/50')}>{o.skip_reason || '—'}</td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/70">{shortenAddress(o.trader_wallet)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
