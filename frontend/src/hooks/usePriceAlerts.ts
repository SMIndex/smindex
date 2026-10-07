import { useEffect, useRef } from 'react';
import { useMarketStore } from '@/stores/marketStore';
import { usePriceAlertStore } from '@/stores/priceAlertStore';

export function usePriceAlerts() {
  const markets = useMarketStore((s) => s.markets);
  const alerts = usePriceAlertStore((s) => s.alerts);
  const markTriggered = usePriceAlertStore((s) => s.markTriggered);
  const notifiedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    for (const alert of alerts) {
      if (alert.triggered) continue;
      if (notifiedRef.current.has(alert.id)) continue;

      const market = markets[alert.marketId];
      if (!market) continue;

      const price = market.mark_price;
      const hit =
        (alert.direction === 'above' && price >= alert.targetPrice) ||
        (alert.direction === 'below' && price <= alert.targetPrice);

      if (hit) {
        notifiedRef.current.add(alert.id);
        markTriggered(alert.id);

        // Browser notification
        if ('Notification' in window && Notification.permission === 'granted') {
          new Notification(`${alert.symbol} Price Alert`, {
            body: `${alert.symbol} is now $${price.toFixed(2)} (${alert.direction} $${alert.targetPrice})`,
            icon: '/favicon.ico',
          });
        }
      }
    }
  }, [markets, alerts, markTriggered]);

  // Request notification permission on first use
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);
}
