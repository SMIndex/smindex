import type { MarketAgg } from '@/lib/copyPortfolio';
import { MARKETS } from '@/config/constants';
import { formatUSD } from '@/lib/formatters';

interface Props {
  rows: MarketAgg[];
}

const DASH = <span className="text-text-secondary/40">—</span>;
function pnl(v: number | null) {
  if (v == null) return DASH;
  return <span className={v >= 0 ? 'text-success' : 'text-danger'}>{`${v >= 0 ? '+' : ''}${formatUSD(v)}`}</span>;
}

export default function CopyMarketBreakdown({ rows }: Props) {
  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
      <h3 className="text-sm font-semibold text-text-primary mb-3">Market Breakdown</h3>
      {!rows.length ? (
        <div className="text-center text-xs text-text-secondary/60 py-6">No copied markets yet.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-secondary/60 text-left border-b border-text-secondary/10">
                <th className="py-2 px-2 font-medium">Market</th>
                <th className="py-2 px-2 font-medium text-right">Trades</th>
                <th className="py-2 px-2 font-medium text-right">Realized</th>
                <th className="py-2 px-2 font-medium text-right">Unreal.</th>
                <th className="py-2 px-2 font-medium text-right">Win%</th>
                <th className="py-2 px-2 font-medium text-right">Alloc</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.market_id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                  <td className="py-2.5 px-2 font-semibold text-text-primary">{r.symbol || MARKETS[r.market_id]?.symbol || r.market_id}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.trades}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums font-semibold">{pnl(r.realized)}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums">{pnl(r.unrealized)}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{r.winRate == null ? '—' : `${r.winRate.toFixed(0)}%`}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums text-text-secondary">{formatUSD(r.allocation)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
