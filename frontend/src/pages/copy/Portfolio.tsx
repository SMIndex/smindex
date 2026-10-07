import { useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useMarketData } from '@/hooks/useMarketData';
import { useCopyPortfolio } from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import ConnectPrompt from '@/components/copy/ConnectPrompt';
import PortfolioSummaryCards from '@/components/copy/PortfolioSummaryCards';
import CopyPnlOverview from '@/components/copy/CopyPnlOverview';
import OpenCopiedPositionsTable from '@/components/copy/OpenCopiedPositionsTable';
import ClosedCopiedPositionsTable from '@/components/copy/ClosedCopiedPositionsTable';
import CopyTraderBreakdown from '@/components/copy/CopyTraderBreakdown';
import CopyMarketBreakdown from '@/components/copy/CopyMarketBreakdown';
import {
  buildSummary, buildTraderBreakdown, buildMarketBreakdown, buildCumulativeRealized,
  type CopySource,
} from '@/lib/copyPortfolio';

type SourceFilter = 'all' | CopySource;

function Card({ children }: { children: ReactNode }) {
  return <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">{children}</div>;
}

function PortfolioInner() {
  // Prime the shared market store with the SAME real mark source used across the app
  // (/api/markets + market_state WS) so live unrealized PnL can be computed here.
  // If a mark is still unavailable, positions keep showing "unavailable" (never 0).
  useMarketData();
  const pf = useCopyPortfolio();
  const [src, setSrc] = useState<SourceFilter>('all');

  const positions = useMemo(
    () => (src === 'all' ? pf.positions : pf.positions.filter((p) => p.source === src)),
    [pf.positions, src],
  );
  const summary = useMemo(() => buildSummary(positions), [positions]);
  const traderRows = useMemo(() => buildTraderBreakdown(positions), [positions]);
  const marketRows = useMemo(() => buildMarketBreakdown(positions), [positions]);
  const cumulative = useMemo(() => buildCumulativeRealized(positions), [positions]);
  const open = useMemo(() => positions.filter((p) => p.is_open), [positions]);
  const closed = useMemo(() => positions.filter((p) => !p.is_open), [positions]);

  if (pf.error) {
    return (
      <Card>
        <div className="text-center py-8 space-y-2">
          <div className="text-sm text-danger">Couldn’t load your copy portfolio.</div>
          <button onClick={pf.refresh} className="text-xs px-3 py-1.5 rounded-lg bg-accent text-white">Retry</button>
        </div>
      </Card>
    );
  }

  if (!pf.loading && pf.positions.length === 0) {
    return (
      <Card>
        <div className="text-center py-12 space-y-3">
          <div className="text-sm font-semibold text-text-primary">No copied positions yet</div>
          <p className="text-xs text-text-secondary/70 max-w-sm mx-auto">
            When you copy a trader, their positions and outcomes appear here with realized/unrealized PnL and estimated fees.
          </p>
          <Link to="/copy/discover" className="inline-block text-xs px-4 py-2 rounded-lg bg-accent text-white font-medium">
            Discover traders
          </Link>
        </div>
      </Card>
    );
  }

  const TABS: { key: SourceFilter; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'paper', label: 'Paper' },
    { key: 'live', label: 'Live' },
  ];

  return (
    <div className="space-y-5">
      <PortfolioSummaryCards summary={summary} subscribedAllocation={pf.subscribedAllocation} />

      <CopyPnlOverview points={cumulative} />

      <div className="flex items-center gap-1 p-1 bg-bg-secondary/80 rounded-xl border border-text-secondary/5 w-fit">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setSrc(t.key)}
            className={clsx('px-4 py-1.5 rounded-lg text-xs font-medium transition-colors',
              src === t.key ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}>
            {t.label}
          </button>
        ))}
      </div>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-3">Open Copied Positions</h3>
        <OpenCopiedPositionsTable positions={open} loading={pf.loading} />
      </Card>

      <Card>
        <h3 className="text-sm font-semibold text-text-primary mb-3">Closed Copied Positions</h3>
        <ClosedCopiedPositionsTable positions={closed} loading={pf.loading} />
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <CopyTraderBreakdown rows={traderRows} />
        <CopyMarketBreakdown rows={marketRows} />
      </div>

      <p className="text-[10px] text-text-secondary/50">
        Fees and net PnL are <span className="font-medium">estimates</span> — copy fills don’t store a fee, so open-side taker fees are
        estimated (close fee = 0). Live unrealized PnL uses the live mark; it shows <span className="italic">unavailable</span> when a mark isn’t cached.
      </p>
    </div>
  );
}

export default function CopyPortfolioPage() {
  const { isAuthenticated } = useAuth();
  return (
    <CopyLayout>
      {isAuthenticated ? <PortfolioInner /> : <ConnectPrompt message="Connect your wallet to view your copy portfolio." />}
    </CopyLayout>
  );
}
