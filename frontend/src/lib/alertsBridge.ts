import api from '@/lib/api';

// Bridges trading events to the server-side Telegram alerts (overnight Part B).
// perplTrading dispatches these AFTER the venue ack; nothing here can delay or
// alter an order. Every call is fire-and-forget; failures are logged only.

let installed = false;

export function installAlertsBridge(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('perpl_sltp_placed', (e: any) => {
    const d = e?.detail;
    if (!d?.oid || !(d.triggerPrice > 0)) return;
    api.post('/api/telegram/triggers', {
      oid: String(d.oid), market_id: d.marketId, side: d.side, kind: d.kind,
      trigger_px: d.triggerPrice, size: d.size,
    }).catch((err) => console.warn('[alerts] trigger register failed', err?.response?.status));
  });
  window.addEventListener('perpl_order_cancelled', (e: any) => {
    const oid = e?.detail?.oid;
    if (oid == null) return;
    api.delete(`/api/telegram/triggers/${encodeURIComponent(String(oid))}`)
      .catch((err) => console.warn('[alerts] trigger unregister failed', err?.response?.status));
  });
}

export function reportLeftoverCancelled(symbol: string, marketId: number, count: number, reason: string): void {
  if (count <= 0) return;
  api.post('/api/telegram/events', { type: 'tpsl_leftover', symbol, market_id: marketId, count, reason })
    .catch((err) => console.warn('[alerts] leftover report failed', err?.response?.status));
}
