import type { RiskEvent } from '@/lib/copyApi';
import { formatTimeAgo } from '@/lib/formatters';

interface Props {
  events: RiskEvent[];
  loading?: boolean;
}

function label(t: string): string {
  return t.replace(/_/g, ' ');
}

export default function RiskEventsPanel({ events, loading }: Props) {
  if (loading) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">Loading risk events…</div>;
  }
  if (!events.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No risk events. These log every time a copy was blocked or limited by your risk settings.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {events.map((e) => (
        <div key={e.id} className="flex items-start gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/40 border border-text-secondary/10">
          <div className="w-7 h-7 rounded-full bg-warning/10 border border-warning/20 flex items-center justify-center shrink-0">
            <svg className="w-3.5 h-3.5 text-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4a2 2 0 00-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
            </svg>
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-text-primary capitalize">{label(e.event_type)}</span>
              <span className="text-[10px] text-text-secondary/50 whitespace-nowrap">{e.created_at ? formatTimeAgo(e.created_at) : ''}</span>
            </div>
            {e.detail && (
              <pre className="text-[10px] text-text-secondary/60 mt-1 whitespace-pre-wrap break-words font-mono">
                {JSON.stringify(e.detail)}
              </pre>
            )}
            {e.subscription_id != null && (
              <span className="text-[10px] text-text-secondary/40">subscription #{e.subscription_id}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
