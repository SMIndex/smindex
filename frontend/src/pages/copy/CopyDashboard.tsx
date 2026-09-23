import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import {
  useSubscriptions,
  useCopyPositions,
  useCopyOrders,
  useLivePositions,
  useRiskEvents,
  useAuditLogs,
} from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import ConnectPrompt from '@/components/copy/ConnectPrompt';
import { useIsMobile } from '@/hooks/useIsMobile';
import MobileCopy from '@/components/copy/mobile/MobileCopy';
import PaperCopySubscriptionCard from '@/components/copy/PaperCopySubscriptionCard';
import PaperPositionsTable from '@/components/copy/PaperPositionsTable';
import PaperOrdersTable from '@/components/copy/PaperOrdersTable';
import LiveOrdersTable from '@/components/copy/LiveOrdersTable';
import LivePositionsTable from '@/components/copy/LivePositionsTable';
import RiskEventsPanel from '@/components/copy/RiskEventsPanel';
import AuditLogPanel from '@/components/copy/AuditLogPanel';

type Tab = 'subscriptions' | 'positions' | 'orders' | 'risk' | 'audit' | 'sandbox';

function DashboardInner() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('subscriptions');

  const subs = useSubscriptions(true);
  const livePositions = useLivePositions('all');
  const liveOrders = useCopyOrders({ mode: 'live', limit: 100 });
  const paperPositions = useCopyPositions({ status: 'all' });
  const paperOrders = useCopyOrders({ mode: 'paper', limit: 100 });
  const risk = useRiskEvents(50);
  const audit = useAuditLogs(50);

  const activeCount = subs.subscriptions.filter((s) => s.status === 'active').length;

  // VERIFIED = backed by a real Perpl confirmation (non-null perpl_order_id or perpl_fill_id).
  // Unverified rows (legacy phantom fills) must NOT be shown/counted as live or filled.
  const verifiedOrderIds = new Set(
    liveOrders.orders.filter((o) => o.perpl_order_id != null || o.perpl_fill_id != null).map((o) => o.id),
  );
  const isVerifiedPosition = (p: typeof livePositions.positions[number]) =>
    p.copy_order_id != null && verifiedOrderIds.has(p.copy_order_id);
  const verifiedPositions = livePositions.positions.filter(isVerifiedPosition);
  const unverifiedHidden = livePositions.positions.length - verifiedPositions.length;

  const openLive = verifiedPositions.filter((p) => p.status === 'open' || p.status === 'partially_closed');
  const filled = liveOrders.orders.filter(
    (o) => o.status === 'filled' && (o.perpl_order_id != null || o.perpl_fill_id != null),
  ).length;
  const blocked = liveOrders.orders.filter((o) => o.status === 'risk_blocked' || o.status === 'failed').length;

  const TABS: { key: Tab; label: string; count?: number }[] = [
    { key: 'subscriptions', label: 'Subscriptions', count: subs.subscriptions.length },
    { key: 'positions', label: 'Live Positions', count: openLive.length },
    { key: 'orders', label: 'Live Copy Orders', count: liveOrders.orders.length },
    { key: 'risk', label: 'Risk Events', count: risk.events.length },
    { key: 'audit', label: 'Audit Log', count: audit.logs.length },
    { key: 'sandbox', label: 'Paper Sandbox', count: paperPositions.positions.length },
  ];

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Summary label="Active Subscriptions" value={String(activeCount)} />
        <Summary label="Open Live Positions" value={String(openLive.length)} tone={openLive.length > 0 ? 'up' : 'neutral'} />
        <Summary label="Live Orders Filled" value={String(filled)} tone={filled > 0 ? 'up' : 'neutral'} />
        <Summary label="Blocked / Failed" value={String(blocked)} tone={blocked > 0 ? 'down' : 'neutral'} />
      </div>

      {/* Sub-tabs */}
      <div className="flex items-center gap-1 p-1 bg-bg-secondary/80 rounded-xl border border-text-secondary/5 overflow-x-auto scrollbar-hide">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-colors whitespace-nowrap shrink-0',
              tab === t.key ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary hover:bg-bg-card/50',
              t.key === 'sandbox' && tab !== t.key && 'text-text-secondary/50',
            )}
          >
            {t.label}
            {t.count != null && (
              <span className={clsx('text-[10px] px-1.5 rounded-full', tab === t.key ? 'bg-white/20' : 'bg-text-secondary/10')}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
        {tab === 'subscriptions' && (
          subs.loading ? (
            <div className="text-center text-xs text-text-secondary/60 py-10">Loading subscriptions…</div>
          ) : subs.subscriptions.length === 0 ? (
            <div className="text-center text-xs text-text-secondary/60 py-10">
              No copy subscriptions yet. Set one up from{' '}
              <button onClick={() => navigate('/copy/discover')} className="text-accent hover:underline">Discover</button>.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {subs.subscriptions.map((s) => (
                <PaperCopySubscriptionCard key={s.id} sub={s} onPause={subs.pause} onResume={subs.resume} onStop={subs.stop} />
              ))}
            </div>
          )
        )}
        {tab === 'positions' && (
          <div className="space-y-3">
            {unverifiedHidden > 0 && (
              <div className="px-3 py-2 rounded-lg bg-warning/5 border border-warning/20 text-[11px] text-text-secondary">
                <span className="font-semibold text-warning">{unverifiedHidden} unverified copy attempt{unverifiedHidden > 1 ? 's were' : ' was'} hidden</span>{' '}
                because {unverifiedHidden > 1 ? 'they are' : 'it is'} not confirmed on Perpl (no Perpl order/fill id).
              </div>
            )}
            <LivePositionsTable
              positions={verifiedPositions}
              loading={livePositions.loading}
              onRefresh={() => { livePositions.refresh(); liveOrders.refresh(); }}
            />
          </div>
        )}
        {tab === 'orders' && <LiveOrdersTable orders={liveOrders.orders} loading={liveOrders.loading} />}
        {tab === 'risk' && <RiskEventsPanel events={risk.events} loading={risk.loading} />}
        {tab === 'audit' && <AuditLogPanel logs={audit.logs} loading={audit.loading} />}
        {tab === 'sandbox' && (
          <div className="space-y-4">
            <div className="px-3 py-2 rounded-lg bg-bg-secondary/60 border border-text-secondary/10 text-[11px] text-text-secondary">
              <span className="font-semibold text-text-primary">Sandbox (simulated)</span> — optional paper records for testing. Not the live product; no real orders.
            </div>
            <div>
              <div className="text-xs font-semibold text-text-secondary mb-2">Paper Positions</div>
              <PaperPositionsTable positions={paperPositions.positions} loading={paperPositions.loading} />
            </div>
            <div>
              <div className="text-xs font-semibold text-text-secondary mb-2">Paper Orders</div>
              <PaperOrdersTable orders={paperOrders.orders} loading={paperOrders.loading} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Summary({ label, value, sub, tone = 'neutral' }: { label: string; value: string; sub?: string; tone?: 'up' | 'down' | 'neutral' }) {
  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-3">
      <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">{label}</div>
      <div className={clsx('text-base font-bold tabular-nums mt-0.5', tone === 'up' ? 'text-success' : tone === 'down' ? 'text-danger' : 'text-text-primary')}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-text-secondary/50 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function CopyDashboardPage() {
  const isMobile = useIsMobile();
  const { isAuthenticated } = useAuth();
  if (isMobile) return <MobileCopy initialTab="dashboard" />;
  return (
    <CopyLayout>
      {isAuthenticated ? <DashboardInner /> : <ConnectPrompt message="Connect to view your copy dashboard." />}
    </CopyLayout>
  );
}
