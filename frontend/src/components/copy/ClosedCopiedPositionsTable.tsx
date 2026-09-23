import { clsx } from 'clsx';
import type { NormalizedCopyPosition } from '@/lib/copyPortfolio';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatPrice } from '@/lib/formatters';
import { shortenAddress } from '@/lib/formatters';

interface Props {
  positions: NormalizedCopyPosition[];
  loading?: boolean;
}

const DASH = <span className="text-text-secondary/40">—</span>;

function pnlCell(v: number | null) {
  if (v == null) return DASH;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${formatUSD(v)}`}</span>;
}
function roiCell(v: number | null) {
  if (v == null) return DASH;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${v.toFixed(2)}%`}</span>;
}
function duration(a: string | null, b: string | null): string {
  if (!a || !b) return '—';
  const ms = new Date(b).getTime() - new Date(a).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export default function ClosedCopiedPositionsTable({ positions, loading }: Props) {
  if (loading) return <div className="text-center text-xs text-text-secondary/60 py-8">Loading…</div>;
  if (!positions.length) {
    return <div className="text-center text-xs text-text-secondary/60 py-8">No closed copied positions yet.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
            <th className="py-2 px-2 font-medium">Trader</th>
            <th className="py-2 px-2 font-medium">Market</th>
            <th className="py-2 px-2 font-medium">Side</th>
            <th className="py-2 px-2 font-medium text-right">Entry</th>
            <th className="py-2 px-2 font-medium text-right">Exit</th>
            <th className="py-2 px-2 font-medium text-right">Realized</th>
            <th className="py-2 px-2 font-medium text-right">Est. Fee</th>
            <th className="py-2 px-2 font-medium text-right">Est. Net</th>
            <th className="py-2 px-2 font-medium text-right">ROI</th>
            <th className="py-2 px-2 font-medium">Duration</th>
            <th className="py-2 px-2 font-medium">Close reason</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const dec = MARKETS[p.market_id]?.decimals ?? 2;
            return (
              <tr key={p.key} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="py-2.5 px-2 font-mono text-text-secondary/80">
                  <span className={clsx('text-[9px] font-semibold px-1.5 py-0.5 rounded uppercase mr-1.5',
                    p.source === 'live' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary')}>{p.source}</span>
                  {shortenAddress(p.trader_wallet)}
                </td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{p.symbol || MARKETS[p.market_id]?.symbol || p.market_id}</td>
                <td className="py-2.5 px-2"><span className={clsx('font-medium', p.side === 'long' ? 'text-success' : 'text-danger')}>{p.side?.toUpperCase()}</span></td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.entry_price != null ? formatPrice(p.entry_price, dec) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{p.close_price != null ? formatPrice(p.close_price, dec) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnlCell(p.realized_pnl)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary/80">{p.est_open_fee != null ? formatUSD(p.est_open_fee) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnlCell(p.est_net_pnl)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums">{roiCell(p.roi)}</td>
                <td className="py-2.5 px-2 text-text-secondary/70">{duration(p.opened_at, p.closed_at)}</td>
                <td className="py-2.5 px-2 text-text-secondary/70">{p.close_reason ? p.close_reason.replace(/_/g, ' ') : DASH}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
