import { useMemo, type ReactNode } from 'react';
import { clsx } from 'clsx';
import type { HistoryRow } from '@/lib/copyPortfolio';
import type { AuditLog } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { formatTimeAgo } from '@/lib/formatters';

interface Props {
  row: HistoryRow | null;
  auditLogs: AuditLog[];
  onClose: () => void;
}

function Field({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-text-secondary/5">
      <span className="text-[11px] text-text-secondary/70">{label}</span>
      <span className={clsx('text-xs text-text-primary text-right break-all', mono && 'font-mono')}>{value ?? <span className="text-text-secondary/40">—</span>}</span>
    </div>
  );
}

export default function CopyHistoryRowDrawer({ row, auditLogs, onClose }: Props) {
  const related = useMemo(() => {
    if (!row) return [];
    const trader = row.trader_wallet.toLowerCase();
    return auditLogs.filter((l) => {
      if (row.copy_order_id != null && l.entity_id === row.copy_order_id) return true;
      const d = l.detail || {};
      const dt = (d.trader_wallet || d.trader || '').toString().toLowerCase();
      return dt && dt === trader;
    });
  }, [row, auditLogs]);

  if (!row) return null;
  const market = MARKETS[row.market_id]?.symbol || row.symbol || `MKT-${row.market_id}`;

  return (
    <div className="fixed inset-0 z-[95] flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" />
      <div className="relative w-full max-w-md h-full bg-bg-card border-l border-text-secondary/10 shadow-2xl overflow-y-auto animate-slideInRight" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-bg-card border-b border-text-secondary/10 px-5 py-4 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-text-primary">{market} {row.side?.toUpperCase()}</div>
            <div className="text-[11px] text-text-secondary/60 capitalize">{row.kind} · {row.mode} · {row.status}</div>
          </div>
          <button onClick={onClose} className="text-text-secondary hover:text-text-primary text-lg leading-none">✕</button>
        </div>

        <div className="px-5 py-4 pb-[calc(1rem+env(safe-area-inset-bottom))] space-y-4">
          <section>
            <h4 className="text-[11px] font-semibold text-text-secondary/60 uppercase tracking-wide mb-1">Copy details</h4>
            <Field label="Source trader" value={row.trader_wallet} mono />
            <Field label="Follower wallet" value={row.follower_wallet} mono />
            <Field label="Copy order id" value={row.copy_order_id != null ? `#${row.copy_order_id}` : null} mono />
            <Field label="Perpl order id" value={row.perpl_order_id} mono />
            <Field label="Perpl fill id" value={row.perpl_fill_id} mono />
            <Field label="Perpl request id" value={row.perpl_request_id} mono />
          </section>

          <section>
            <h4 className="text-[11px] font-semibold text-text-secondary/60 uppercase tracking-wide mb-1">Outcome</h4>
            <Field label="Close reason" value={row.close_reason ? row.close_reason.replace(/_/g, ' ') : null} />
            <Field label="Skip / risk reason" value={row.skip_reason ? row.skip_reason.replace(/_/g, ' ') : null} />
            <Field label="Error" value={row.error_message} />
            {(row.est_fee != null || row.est_net_pnl != null) && (
              <div className="text-[10px] text-text-secondary/50 mt-1">Fees &amp; net PnL are estimates (open-side taker; close = 0).</div>
            )}
          </section>

          <section>
            <h4 className="text-[11px] font-semibold text-text-secondary/60 uppercase tracking-wide mb-1">Related audit log</h4>
            {related.length === 0 ? (
              <div className="text-[11px] text-text-secondary/50 py-2">No related audit entries.</div>
            ) : (
              <div className="space-y-1.5">
                {related.map((l) => (
                  <div key={l.id} className="flex items-start justify-between gap-2 text-[11px] py-1 border-b border-text-secondary/5">
                    <span className="text-text-primary">{l.action.replace(/_/g, ' ')}</span>
                    <span className="text-text-secondary/50 shrink-0">{l.created_at ? formatTimeAgo(l.created_at) : ''}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
