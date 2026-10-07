import { clsx } from 'clsx';
import type { HistoryRow } from '@/lib/copyPortfolio';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';
import { utcDateTime } from '@/lib/time';

interface Props {
  rows: HistoryRow[];
  loading?: boolean;
  onRowClick: (row: HistoryRow) => void;
  truncated?: boolean;
}

const DASH = <span className="text-text-secondary/40">—</span>;

function pnlCell(v: number | null) {
  if (v == null) return DASH;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${formatUSD(v)}`}</span>;
}

const STATUS_STYLE: Record<string, string> = {
  filled: 'text-success bg-success/10',
  submitted: 'text-accent bg-accent/10',
  simulated: 'text-accent bg-accent/10',
  closed: 'text-text-secondary bg-text-secondary/10',
  liquidated: 'text-danger bg-danger/10',
  failed: 'text-danger bg-danger/10',
  risk_blocked: 'text-danger bg-danger/10',
  skipped: 'text-text-secondary bg-text-secondary/10',
};

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  return Number.isNaN(d.getTime()) ? '—' : utcDateTime(d.getTime() / 1000);
}

export default function CopyHistoryTable({ rows, loading, onRowClick, truncated }: Props) {
  if (loading) return <div className="text-center text-xs text-text-secondary/60 py-10">Loading history…</div>;
  if (!rows.length) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">No copy history matches these filters.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs whitespace-nowrap">
        <thead>
          <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
            <th className="py-2 px-2 font-medium">Date</th>
            <th className="py-2 px-2 font-medium">Mode</th>
            <th className="py-2 px-2 font-medium">Trader</th>
            <th className="py-2 px-2 font-medium">Market</th>
            <th className="py-2 px-2 font-medium">Side</th>
            <th className="py-2 px-2 font-medium">Status</th>
            <th className="py-2 px-2 font-medium text-right">Entry</th>
            <th className="py-2 px-2 font-medium text-right">Exit</th>
            <th className="py-2 px-2 font-medium text-right">Size</th>
            <th className="py-2 px-2 font-medium text-right">Lev</th>
            <th className="py-2 px-2 font-medium text-right">Alloc</th>
            <th className="py-2 px-2 font-medium text-right">Realized</th>
            <th className="py-2 px-2 font-medium text-right">Est. Fee</th>
            <th className="py-2 px-2 font-medium text-right">Est. Net</th>
            <th className="py-2 px-2 font-medium text-right">ROI</th>
            <th className="py-2 px-2 font-medium">Order id</th>
            <th className="py-2 px-2 font-medium">Close</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const dec = MARKETS[r.market_id]?.decimals ?? 2;
            const orderId = r.perpl_order_id || (r.copy_order_id != null ? `#${r.copy_order_id}` : null);
            return (
              <tr key={r.key} onClick={() => onRowClick(r)} className="border-b border-text-secondary/5 hover:bg-bg-secondary/40 cursor-pointer">
                <td className="py-2.5 px-2 text-text-secondary/80">{fmtDate(r.created_at)}</td>
                <td className="py-2.5 px-2">
                  <span className={clsx('text-[9px] font-semibold px-1.5 py-0.5 rounded',
                    r.source === 'live' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary')}>
                    {r.mode}
                  </span>
                  <span className="ml-1 text-[9px] text-text-secondary/40 uppercase">{r.kind}</span>
                </td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/80">{shortenAddress(r.trader_wallet)}</td>
                <td className="py-2.5 px-2 font-semibold text-text-primary">{r.symbol || MARKETS[r.market_id]?.symbol || r.market_id}</td>
                <td className="py-2.5 px-2"><span className={clsx('font-medium', r.side === 'long' ? 'text-success' : 'text-danger')}>{r.side?.toUpperCase()}</span></td>
                <td className="py-2.5 px-2"><span className={clsx('text-[10px] font-medium px-1.5 py-0.5 rounded', STATUS_STYLE[r.status] || 'text-text-secondary bg-text-secondary/10')}>{r.status}</span></td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.entry_price != null ? formatPrice(r.entry_price, dec) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.exit_price != null ? formatPrice(r.exit_price, dec) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.size != null ? formatSize(r.size) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.leverage != null ? `${r.leverage}x` : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.allocation_usd != null ? formatUSD(r.allocation_usd) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnlCell(r.realized_pnl)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary/80">{r.est_fee != null ? formatUSD(r.est_fee) : DASH}</td>
                <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnlCell(r.est_net_pnl)}</td>
                <td className="py-2.5 px-2 text-right tabular-nums">{r.roi == null ? DASH : <span className={r.roi >= 0 ? 'text-success' : 'text-danger'}>{`${r.roi >= 0 ? '+' : ''}${r.roi.toFixed(1)}%`}</span>}</td>
                <td className="py-2.5 px-2 font-mono text-text-secondary/60">{orderId ? (orderId.length > 12 ? `${orderId.slice(0, 10)}…` : orderId) : DASH}</td>
                <td className="py-2.5 px-2 text-text-secondary/70">{r.close_reason ? r.close_reason.replace(/_/g, ' ') : DASH}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {truncated && (
        <div className="text-center text-[10px] text-text-secondary/50 py-3">
          Showing the most recent records only. Narrow the filters to see specific trades.
        </div>
      )}
    </div>
  );
}
