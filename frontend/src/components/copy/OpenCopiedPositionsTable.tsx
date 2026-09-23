import { clsx } from 'clsx';
import type { NormalizedCopyPosition } from '@/lib/copyPortfolio';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatPrice, formatSize, formatTimeAgo, shortenAddress } from '@/lib/formatters';

interface Props {
  positions: NormalizedCopyPosition[];
  loading?: boolean;
}

const DASH = <span className="text-text-secondary/40">—</span>;
const UNAVAIL = <span className="text-text-secondary/40 italic">unavailable</span>;

function pnlCell(v: number | null) {
  if (v == null) return UNAVAIL;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${formatUSD(v)}`}</span>;
}
function roiCell(v: number | null) {
  if (v == null) return DASH;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${v.toFixed(2)}%`}</span>;
}

function SourceBadge({ source }: { source: 'paper' | 'live' }) {
  return (
    <span className={clsx('text-[9px] font-semibold px-1.5 py-0.5 rounded uppercase',
      source === 'live' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary')}>
      {source}
    </span>
  );
}

export default function OpenCopiedPositionsTable({ positions, loading }: Props) {
  if (loading) return <div className="text-center text-xs text-text-secondary/60 py-8">Loading positions…</div>;
  if (!positions.length) {
    return <div className="text-center text-xs text-text-secondary/60 py-8">No open copied positions.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
            <th className="py-2 px-2 font-medium">Trader</th>
            <th className="py-2 px-2 font-medium">Market</th>
            <th className="py-2 px-2 font-medium">Side</th>
            <th className="py-2 px-2 font-medium text-right">Size</th>
            <th className="py-2 px-2 font-medium text-right">Entry</th>
            <th className="py-2 px-2 font-medium text-right">Mark</th>
            <th className="py-2 px-2 font-medium text-right">Lev</th>
            <th className="py-2 px-2 font-medium text-right">Alloc</th>
            <th className="py-2 px-2 font-medium text-right">uPnL</th>
            <th className="py-2 px-2 font-medium text-right">ROI</th>
            <th className="py-2 px-2 font-medium">Opened</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const dec = MARKETS[p.market_id]?.decimals ?? 2;
            return (
              <tr key={p.key} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="py-2.5 px-2 font-mono text-text-secondary/80">
                  <div className="flex items-center gap-1.5"><SourceBadge source={p.source} />{shortenAddress(p.trader_wallet)}</div>
                </td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol || MARKETS[p.market_id]?.symbol || p.market_id}</td>
                <td className="py-2.5 px-2"><span className={clsx('font-medium', p.side === 'long' ? 'text-success' : 'text-danger')}>{p.side?.toUpperCase()}</span></td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-primary">{p.size != null ? formatSize(p.size) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.entry_price != null ? formatPrice(p.entry_price, dec) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.mark_price != null ? formatPrice(p.mark_price, dec) : UNAVAIL}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.leverage != null ? `${p.leverage}x` : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.allocation_usd != null ? formatUSD(p.allocation_usd) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnlCell(p.unrealized_pnl)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{roiCell(p.roi)}</td>
                <td className="py-2.5 px-2 text-text-secondary/70">{p.opened_at ? formatTimeAgo(p.opened_at) : DASH}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
