import type { AuditLog } from '@/lib/copyApi';
import { formatTimeAgo } from '@/lib/formatters';

interface Props {
  logs: AuditLog[];
  loading?: boolean;
}

function actionLabel(a: string): string {
  return a.replace(/_/g, ' ');
}

export default function AuditLogPanel({ logs, loading }: Props) {
  if (loading) {
    return <div className="text-center text-xs text-text-secondary/60 py-10">Loading audit log…</div>;
  }
  if (!logs.length) {
    return (
      <div className="text-center text-xs text-text-secondary/60 py-10">
        No activity yet. Every watch, subscribe, pause, resume and stop is recorded here.
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {logs.map((l) => (
        <div key={l.id} className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-bg-secondary/30 transition-colors">
          <span className="w-1.5 h-1.5 rounded-full bg-accent/50 shrink-0" />
          <span className="text-xs font-medium text-text-primary capitalize whitespace-nowrap">{actionLabel(l.action)}</span>
          {l.entity_type && (
            <span className="text-[11px] text-text-secondary/60">
              {l.entity_type.replace(/_/g, ' ')}{l.entity_id != null ? ` #${l.entity_id}` : ''}
            </span>
          )}
          {l.detail && (
            <span className="text-[10px] text-text-secondary/40 font-mono truncate flex-1">{JSON.stringify(l.detail)}</span>
          )}
          <span className="text-[10px] text-text-secondary/50 whitespace-nowrap ml-auto">{l.created_at ? formatTimeAgo(l.created_at) : ''}</span>
        </div>
      ))}
    </div>
  );
}
