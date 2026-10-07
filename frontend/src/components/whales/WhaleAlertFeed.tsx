import { useState, useRef, useEffect } from 'react';
import { useWhaleAlerts } from '@/hooks/useWhaleAlerts';
import WhaleAlertCard from '@/components/whales/WhaleAlertCard';

interface WhaleAlertFeedProps {
  marketId?: number;
}

type AlertFilter = 'all' | 'large_order' | 'large_fill' | 'oi_divergence';

export default function WhaleAlertFeed({ marketId }: WhaleAlertFeedProps) {
  const { alerts } = useWhaleAlerts(marketId);
  const [filter, setFilter] = useState<AlertFilter>('all');
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(alerts.length);

  useEffect(() => {
    if (alerts.length > prevCountRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
    prevCountRef.current = alerts.length;
  }, [alerts.length]);

  const filteredAlerts =
    filter === 'all'
      ? alerts
      : alerts.filter((a) => a.alert_type === filter);

  const filters: { value: AlertFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'large_order', label: 'Orders' },
    { value: 'large_fill', label: 'Fills' },
    { value: 'oi_divergence', label: 'OI' },
  ];

  return (
    <div>
      <div className="flex items-center gap-1 mb-3">
        {filters.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              filter === f.value
                ? 'bg-accent/10 text-accent'
                : 'text-text-secondary hover:text-text-primary hover:bg-bg-secondary'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div
        ref={scrollRef}
        className="max-h-[400px] overflow-y-auto space-y-2 pr-1"
      >
        {filteredAlerts.length === 0 ? (
          <div className="text-sm text-text-secondary text-center py-8">
            No alerts to display
          </div>
        ) : (
          filteredAlerts.map((alert) => (
            <WhaleAlertCard key={alert.id} alert={alert} />
          ))
        )}
      </div>
    </div>
  );
}
