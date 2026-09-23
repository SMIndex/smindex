import { useRef, useEffect } from 'react';
import { clsx } from 'clsx';
import { useNotificationStore, type NotificationType } from '@/stores/notificationStore';

const typeIcons: Record<NotificationType, { icon: string; color: string }> = {
  sl_tp_triggered: {
    icon: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z',
    color: 'text-warning',
  },
  copy_updates: {
    icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z',
    color: 'text-accent',
  },
  whale_alerts: {
    icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
    color: 'text-success',
  },
};

function formatTime(ts: number): string {
  const diff = Date.now() - ts;
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return new Date(ts).toLocaleDateString();
}

export default function NotificationPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const { notifications, markAllRead, clearAll } = useNotificationStore();

  useEffect(() => {
    if (!open) return;
    const handle = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open, onClose]);

  useEffect(() => {
    if (open) markAllRead();
  }, [open, markAllRead]);

  if (!open) return null;

  return (
    <div
      ref={ref}
      className="absolute top-full right-0 mt-1.5 w-[calc(100vw-24px)] sm:w-80 max-h-[420px] bg-bg-card/95 backdrop-blur-xl border border-text-secondary/15 rounded-xl shadow-2xl shadow-black/30 z-50 animate-[fadeIn_0.15s_ease-out] flex flex-col"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-text-secondary/10">
        <span className="text-xs font-semibold text-text-primary">Notifications</span>
        {notifications.length > 0 && (
          <button
            onClick={clearAll}
            className="text-[10px] text-text-secondary hover:text-danger transition-colors"
          >
            Clear all
          </button>
        )}
      </div>

      {/* List */}
      <div className="overflow-y-auto flex-1">
        {notifications.length === 0 ? (
          <div className="py-10 text-center text-xs text-text-secondary">No notifications</div>
        ) : (
          notifications.map((n) => {
            const { icon, color } = typeIcons[n.type] || typeIcons.whale_alerts;
            return (
              <div
                key={n.id}
                className={clsx(
                  'flex items-start gap-2.5 px-3 py-2.5 border-b border-text-secondary/5 hover:bg-text-secondary/5 transition-colors',
                  !n.read && 'bg-accent/5',
                )}
              >
                <svg className={clsx('w-4 h-4 mt-0.5 shrink-0', color)} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={icon} />
                </svg>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-text-primary leading-snug">{n.message}</p>
                  <p className="text-[10px] text-text-secondary mt-0.5">{formatTime(n.timestamp)}</p>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
