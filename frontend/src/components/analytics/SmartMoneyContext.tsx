import { useQuery } from '@tanstack/react-query';
import { getAnalyticsContext, type ContextCrowd } from '@/lib/analyticsApi';
import {
  ctxStanceLine, ctxCrowdLine, ctxFundingLine, ctxUnavailable,
} from '@/lib/analyticsCopy';

// Tier-2 Part B2 — "Smart money context" block for the Copy Live Trade modal.
// DISPLAY ONLY: read-only fetch, renders at most three generated lines from
// the shared template module, and NEVER blocks or gates the order — every
// failure path degrades to the CTX0 unavailable line while the modal keeps
// working. No execution file imports this; this imports no execution file.
export default function SmartMoneyContext({ asset, side }: {
  asset: string;
  side: 'long' | 'short';
}) {
  const q = useQuery({
    queryKey: ['analytics-context', asset],
    queryFn: () => getAnalyticsContext(asset),
    staleTime: 60_000,
    retry: 1,
  });

  const d = q.data;
  const unavailable = q.isError || (d != null && !d.available);
  if (q.isLoading) return null;   // no flash — the modal renders fine without it

  const stanceColor = d?.stance === 'net_long' ? 'var(--green, #22c55e)'
    : d?.stance === 'net_short' ? 'var(--red, #ef4444)'
    : 'rgba(148,163,184,0.5)';

  if (unavailable || !d?.stance) {
    return (
      <div className="p-2.5 bg-bg-secondary rounded-lg text-[11px] text-text-secondary/60"
           style={{ borderLeft: '3px solid rgba(148,163,184,0.35)' }}>
        <span className="text-[10px] uppercase tracking-wide block mb-0.5 text-text-secondary/50">Smart money context</span>
        {ctxUnavailable()}
      </div>
    );
  }

  // biggest crowd across sides (each side carries at most its top cluster)
  const crowds = [d.crowding?.long, d.crowding?.short].filter(Boolean) as ContextCrowd[];
  const crowd = crowds.length
    ? crowds.reduce((a, b) => (b.wallet_count > a.wallet_count ? b : a)) : null;

  const ageMin = d.computed_at
    ? Math.max(0, Math.round((Date.now() - Date.parse(d.computed_at + 'Z')) / 60000)) : null;

  return (
    <div className="p-2.5 bg-bg-secondary rounded-lg"
         style={{ borderLeft: `3px solid ${stanceColor}` }}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] uppercase tracking-wide text-text-secondary/50">Smart money context</span>
        <span className="text-[9.5px] text-text-secondary/40"
              title="From the analytics 20-minute full-cohort sweep (top HL leaderboard wallets, MMs excluded). Descriptive context only — it does not gate this order.">
          {ageMin != null ? `as of ${ageMin < 1 ? 'now' : `${ageMin}m ago`} · 20m sweep` : '20m sweep'}
        </span>
      </div>
      <div className="text-[11px] text-text-secondary leading-relaxed">
        {ctxStanceLine(d.asset, d.stance, d.smi ?? null, !!d.smi_calibrating)}
        {d.low_sample && <span className="text-text-secondary/50"> · small sample ({(d.wallets_long ?? 0) + (d.wallets_short ?? 0)} wallets)</span>}
        {crowd && <> · {ctxCrowdLine(side, crowd)}</>}
        {d.funding_hourly != null && d.funding_hourly !== 0 && <> · {ctxFundingLine(side, d.funding_hourly)}</>}
      </div>
    </div>
  );
}
