import type { HistoryFilters } from '@/lib/copyPortfolio';
import { MARKETS } from '@/config/constants';
import { shortenAddress } from '@/lib/formatters';

interface Props {
  filters: HistoryFilters;
  onChange: (patch: Partial<HistoryFilters>) => void;
  traders: string[];
  markets: number[];
  statuses: string[];
  onReset: () => void;
}

const sel = 'bg-bg-secondary border border-text-secondary/15 rounded-lg text-xs px-2 py-1.5 text-text-primary focus:outline-none focus:border-accent';

export default function CopyHistoryFilters({ filters, onChange, traders, markets, statuses, onReset }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select className={sel} value={filters.trader ?? 'all'} onChange={(e) => onChange({ trader: e.target.value === 'all' ? undefined : e.target.value })}>
        <option value="all">All traders</option>
        {traders.map((t) => <option key={t} value={t}>{shortenAddress(t)}</option>)}
      </select>

      <select className={sel} value={String(filters.market_id ?? 'all')} onChange={(e) => onChange({ market_id: e.target.value === 'all' ? 'all' : Number(e.target.value) })}>
        <option value="all">All markets</option>
        {markets.map((m) => <option key={m} value={m}>{MARKETS[m]?.symbol || `MKT-${m}`}</option>)}
      </select>

      <select className={sel} value={filters.side ?? 'all'} onChange={(e) => onChange({ side: e.target.value as HistoryFilters['side'] })}>
        <option value="all">Any side</option>
        <option value="long">Long</option>
        <option value="short">Short</option>
      </select>

      <select className={sel} value={filters.source ?? 'all'} onChange={(e) => onChange({ source: e.target.value as HistoryFilters['source'] })}>
        <option value="all">Paper + Live</option>
        <option value="paper">Paper</option>
        <option value="live">Live</option>
      </select>

      <select className={sel} value={filters.status ?? 'all'} onChange={(e) => onChange({ status: e.target.value })}>
        <option value="all">Any status</option>
        {statuses.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>

      <select className={sel} value={filters.pnl ?? 'all'} onChange={(e) => onChange({ pnl: e.target.value as HistoryFilters['pnl'] })}>
        <option value="all">Any PnL</option>
        <option value="positive">PnL &gt; 0</option>
        <option value="negative">PnL &lt; 0</option>
      </select>

      <input type="date" className={sel} value={filters.from ?? ''} onChange={(e) => onChange({ from: e.target.value || undefined })} title="From date" />
      <input type="date" className={sel} value={filters.to ?? ''} onChange={(e) => onChange({ to: e.target.value || undefined })} title="To date" />

      <input type="text" className={`${sel} min-w-[150px]`} placeholder="Search order / request id"
        value={filters.search ?? ''} onChange={(e) => onChange({ search: e.target.value || undefined })} />

      <button onClick={onReset} className="text-xs px-3 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-secondary/60 transition-colors">
        Reset
      </button>
    </div>
  );
}
