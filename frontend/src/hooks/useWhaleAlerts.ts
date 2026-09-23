import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAlertStore } from '@/stores/alertStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { getWhaleAlerts } from '@/lib/api';

export function useWhaleAlerts(marketId?: number) {
  const { subscribe, unsubscribe } = useWebSocket();
  const alerts = useAlertStore((s) => s.alerts);
  const addAlert = useAlertStore((s) => s.addAlert);

  const { data } = useQuery({
    queryKey: ['whale-alerts', marketId],
    queryFn: () => getWhaleAlerts({ market_id: marketId, limit: 50 }),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    if (data) {
      const existingIds = new Set(alerts.map((a) => a.id));
      data.forEach((alert) => {
        if (!existingIds.has(alert.id)) {
          addAlert(alert);
        }
      });
    }
    // Only run on data change, not alerts
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, addAlert]);

  useEffect(() => {
    subscribe('whale_alerts', marketId ? { market_id: marketId } : undefined);
    return () => unsubscribe('whale_alerts');
  }, [marketId, subscribe, unsubscribe]);

  const filteredAlerts = marketId
    ? alerts.filter((a) => a.market_id === marketId)
    : alerts;

  return { alerts: filteredAlerts };
}
