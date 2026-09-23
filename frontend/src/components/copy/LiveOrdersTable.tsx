import { clsx } from 'clsx';
import type { CopyOrder } from '@/lib/copyApi';
import { copyOrderReasonLabel } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatSize, formatTimeAgo, shortenAddress } from '@/lib/formatters';

interface Props {
  orders: CopyOrder[];
  loading?: boolean;
}

const STATUS_STYLE: Record<string, string> = {
  filled: 'text-success bg-success/10',
  submitted: 'text-accent bg-accent/10',
  pending_confirmation: 'text-accent bg-accent/10',
  unconfirmed: 'text-warning bg-warning/10',
  failed: 'text-danger bg-danger/10',
  risk_blocked: 'text-warning bg-warning/10',
  skipped: 'text-warning bg-warning/10',
};

// A 'filled' order without a real Perpl order/fill id is NOT a confirmed fill — show it
// honestly as 'unconfirmed' rather than a green "filled" badge.
function displayStatus(o: CopyOrder): string {
  if (o.status === 'filled' && o.perpl_order_id == null && o.perpl_fill_id == null) return 'unconfirmed';
  return o.status;
}

export default function LiveOrdersTable({ orders, loading }: Props) {
  if (loading) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">Loading live copy orders…</div>;
  }
  if (!orders.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No live copy orders yet. Open a trader's profile and click <span className="text-text-primary font-medium">Copy</span> on one of their positions.
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
            <th className="py-2 px-2 font-medium text-right">Size</th>
            <th className="py-2 px-2 font-medium text-right">Lev</th>
            <th className="py-2 px-2 font-medium">Status</th>
            <th className="py-2 px-2 font-medium">Perpl Order</th>
            <th className="py-2 px-2 font-medium">Note</th>
            <th className="py-2 px-2 font-medium">Trader</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => {
            const decimals = MARKETS[o.market_id]?.decimals ?? 2;
            const filledOrIntended = o.fill_size ?? o.intended_size;
            const dispStatus = displayStatus(o);
            const note = o.error_message
              || (dispStatus === 'unconfirmed' ? 'Not confirmed on Perpl' : null)
              || (o.skip_reason ? copyOrderReasonLabel(o.skip_reason, o.status) : '—');
            const isProblem = o.status === 'failed' || o.status === 'risk_blocked' || dispStatus === 'unconfirmed';
            return (
              <tr key={o.id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="py-2.5 px-2 text-text-secondary/70 whitespace-nowrap">{o.created_at ? formatTimeAgo(o.created_at) : '—'}</td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{o.symbol || MARKETS[o.market_id]?.symbol || o.market_id}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('font-medium', o.side === 'long' ? 'text-success' : 'text-danger')}>{o.side?.toUpperCase()}</span>
                </td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-primary">{filledOrIntended != null ? formatSize(filledOrIntended) : '—'}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{o.leverage != null ? `${o.leverage}x` : '—'}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded uppercase whitespace-nowrap', STATUS_STYLE[dispStatus] || 'text-text-secondary bg-text-secondary/10')}>
                    {dispStatus.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/80">
                  {o.perpl_order_id ? o.perpl_order_id : (o.fill_price != null ? `@ ${formatPrice(o.fill_price, decimals)}` : '—')}
                </td>
                <td className={clsx('py-2.5 px-2', isProblem ? 'text-warning' : 'text-text-secondary/50')}>{note}</td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/70">{shortenAddress(o.trader_wallet)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
