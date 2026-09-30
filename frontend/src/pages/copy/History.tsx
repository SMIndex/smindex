import { useMemo, useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useMarketData } from '@/hooks/useMarketData';
import { useCopyHistory } from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import ConnectPrompt from '@/components/copy/ConnectPrompt';
import CopyHistoryFilters from '@/components/copy/CopyHistoryFilters';
import CopyHistoryTable from '@/components/copy/CopyHistoryTable';
import CopyHistoryRowDrawer from '@/components/copy/CopyHistoryRowDrawer';
import { applyHistoryFilters, type HistoryFilters, type HistoryRow } from '@/lib/copyPortfolio';

// Backend caps /api/copy/orders and /api/copy/audit-logs at limit<=100 (Query le=100);
// requesting more returns 422. Fetch the max the contract allows.
const LIMIT = 100;

function HistoryInner() {
  // Prime the shared market store with the same real mark source used elsewhere.
  useMarketData();
  const hist = useCopyHistory(LIMIT);
  const [filters, setFilters] = useState<HistoryFilters>({});
  const [selected, setSelected] = useState<HistoryRow | null>(null);

  const traders = useMemo(
    () => Array.from(new Set(hist.rows.map((r) => r.trader_wallet.toLowerCase()))),
    [hist.rows],
  );
  const markets = useMemo(
    () => Array.from(new Set(hist.rows.map((r) => r.market_id))).sort((a, b) => a - b),
    [hist.rows],
  );
  const statuses = useMemo(
    () => Array.from(new Set(hist.rows.map((r) => r.status))).sort(),
    [hist.rows],
  );

  const filtered = useMemo(() => applyHistoryFilters(hist.rows, filters), [hist.rows, filters]);

  const onChange = (patch: Partial<HistoryFilters>) => setFilters((f) => ({ ...f, ...patch }));

  if (hist.error) {
    return (
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4 text-center py-8 space-y-2">
        <div className="text-sm text-danger">Couldn’t load copy history.</div>
        <button onClick={hist.refresh} className="text-xs px-3 py-1.5 rounded-lg bg-accent text-white">Retry</button>
      </div>
    );
  }

  if (!hist.loading && hist.rows.length === 0) {
    return (
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4 text-center py-12">
        <div className="text-sm font-semibold text-text-primary mb-1">No copy history yet</div>
        <p className="text-xs text-text-secondary/70">Copy order attempts and closed outcomes will appear here.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
        <CopyHistoryFilters
          filters={filters} onChange={onChange} traders={traders} markets={markets} statuses={statuses}
          onReset={() => setFilters({})}
        />
      </div>

      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-text-primary">Copy History</h3>
          <span className="text-[11px] text-text-secondary/50">{filtered.length} of {hist.rows.length}</span>
        </div>
        <CopyHistoryTable
          rows={filtered} loading={hist.loading} onRowClick={setSelected}
          truncated={hist.rows.length >= LIMIT}
        />
      </div>

      <p className="text-[10px] text-text-secondary/50">
        Fees &amp; net PnL are estimates (open-side taker; close = 0). Rows are copy order attempts plus closed position outcomes — real data only.
      </p>

      <CopyHistoryRowDrawer row={selected} auditLogs={hist.auditLogs} onClose={() => setSelected(null)} />
    </div>
  );
}

export default function CopyHistoryPage() {
  const { isAuthenticated } = useAuth();
  return (
    <CopyLayout>
      {isAuthenticated ? <HistoryInner /> : <ConnectPrompt message="Connect your wallet to view your copy history." />}
    </CopyLayout>
  );
}
