import { clsx } from 'clsx';
import type { PaperPosition } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';

interface Props {
  positions: PaperPosition[];
  loading?: boolean;
}

const STATUS_STYLE: Record<string, string> = {
  open: 'text-accent bg-accent/10',
  closed: 'text-text-secondary bg-text-secondary/10',
  liquidated: 'text-danger bg-danger/10',
};

function pnlValue(p: PaperPosition): number {
  return p.status === 'open' ? p.unrealized_pnl ?? 0 : p.realized_pnl ?? 0;
}

export default function PaperPositionsTable({ positions, loading }: Props) {
  if (loading) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">Loading paper positions…</div>;
  }
  if (!positions.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No paper positions yet. They appear here when a copied trader opens a position.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
            <th className="py-2 px-2 font-medium">Market</th>
            <th className="py-2 px-2 font-medium">Side</th>
            <th className="py-2 px-2 font-medium text-right">Size</th>
            <th className="py-2 px-2 font-medium text-right">Entry</th>
            <th className="py-2 px-2 font-medium text-right">Mark / Close</th>
            <th className="py-2 px-2 font-medium text-right">Lev</th>
            <th className="py-2 px-2 font-medium text-right">PnL (paper)</th>
            <th className="py-2 px-2 font-medium">Status</th>
            <th className="py-2 px-2 font-medium">Trader</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const decimals = MARKETS[p.market_id]?.decimals ?? 2;
            const pnl = pnlValue(p);
            const pnlUp = pnl >= 0;
            const markOrClose = p.status === 'open' ? p.mark_price : p.close_price;
            return (
              <tr key={p.id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol || MARKETS[p.market_id]?.symbol || p.market_id}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('font-medium', p.side === 'long' ? 'text-success' : 'text-danger')}>
                    {p.side?.toUpperCase()}
                  </span>
                </td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-primary">{p.size != null ? formatSize(p.size) : '—'}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.entry_price != null ? formatPrice(p.entry_price, decimals) : '—'}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{markOrClose != null ? formatPrice(markOrClose, decimals) : '—'}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.leverage != null ? `${p.leverage}x` : '—'}</td>
                <td className={clsx('py-2.5 px-2 text-right tabular-nums font-semibold', pnlUp ? 'text-success' : 'text-danger')}>
                  {formatUSD(pnl)}
                </td>
                <td className="py-2.5 px-2">
                  <span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded uppercase', STATUS_STYLE[p.status] || STATUS_STYLE.closed)}>
                    {p.status}
                  </span>
                </td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/70">{shortenAddress(p.trader_wallet)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
