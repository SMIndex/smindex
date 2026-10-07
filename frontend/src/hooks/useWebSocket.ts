import { useEffect, useCallback } from 'react';
import { WS_BASE } from '@/config/constants';
import { ReconnectingWebSocket } from '@/lib/ws';
import { useAuthStore } from '@/stores/authStore';
import { useMarketStore } from '@/stores/marketStore';
import { useHeatmapStore } from '@/stores/heatmapStore';
import { useAlertStore } from '@/stores/alertStore';
import { useNotificationStore } from '@/stores/notificationStore';
import { useSoundStore } from '@/stores/soundStore';
import { playSlTpTriggered, playLeaderTrade } from '@/lib/sounds';
import { shortenAddress } from '@/lib/formatters';

interface Subscription {
  channel: string;
  params?: Record<string, any>;
}

// ── ONE shared /ws/feed socket for the whole app ─────────────────────────────
// Every hook consumer used to open its OWN socket, each receiving a full copy
// of every 3s broadcast — and each dispatching notifications, so alerts fired
// once per mounted consumer. All message handling routes through Zustand
// getState() so a single module-level dispatcher serves every consumer.

let sharedWs: ReconnectingWebSocket | null = null;
let sharedToken: string | null | undefined; // undefined = never connected
let refCount = 0;
const desiredSubs = new Map<string, Subscription>();

function dispatchMessage(data: any) {
  switch (data.channel) {
    case 'market_state':
      if (data.data) useMarketStore.getState().updateMarket(data.data.market_id, data.data);
      break;
    case 'heatmap':
      if (data.data) useHeatmapStore.getState().setHeatmap(data.data.market_id, data.data);
      break;
    case 'whale_alerts':
      if (data.data) {
        useAlertStore.getState().addAlert(data.data);
        const w = data.data;
        useNotificationStore.getState().addNotification('whale_alerts', `Whale ${w.direction || 'trade'}: ${w.size || ''} on market ${w.market_id || ''}`);
      }
      break;
    case 'trader_activity':
      if (data.data) window.dispatchEvent(new CustomEvent('trader_activity', { detail: data.data }));
      break;
    case 'oi_alerts':
      if (data.data) {
        useAlertStore.getState().addAlert(data.data);
        if ('Notification' in window && Notification.permission === 'granted') {
          const d = data.data;
          new Notification(`OI ${d.direction === 'spike_up' ? 'Spike' : 'Drop'}`, {
            body: `${d.change_pct}% OI change ($${d.change_usd}) on market ${d.market_id}`,
          });
        }
      }
      break;
    case 'social_feed':
      if (data.data) window.dispatchEvent(new CustomEvent('social_feed', { detail: data.data }));
      break;
    case 'sl_tp_triggered':
      if (data.data) {
        window.dispatchEvent(new CustomEvent('sl_tp_triggered', { detail: data.data }));
        const sl = data.data;
        useNotificationStore.getState().addNotification('sl_tp_triggered', `${sl.type || 'SL/TP'} triggered: ${sl.side || ''} ${sl.market || ''} at ${sl.price || ''}`);
        if (useSoundStore.getState().enabled) playSlTpTriggered();
      }
      break;
    case 'copy_updates':
      window.dispatchEvent(new CustomEvent('copy_update', { detail: data.data }));
      if (data.data) {
        const cu = data.data;
        const who = cu.wallet ? shortenAddress(cu.wallet) : 'a leader';
        const sym = cu.symbol || '';
        const side = cu.side ? cu.side.toUpperCase() : '';
        let msg: string;
        if (cu.type === 'leader_entry') {
          msg = `${who} opened ${side} ${sym}${cu.entry_price ? ` @ ${cu.entry_price}` : ''}`;
        } else if (cu.type === 'leader_exit') {
          msg = `${who} closed ${side} ${sym}`;
        } else {
          msg = `${who} ${cu.type || 'trade'} ${sym}`.trim();
        }
        useNotificationStore.getState().addNotification('copy_updates', msg);
        if (useSoundStore.getState().enabled) playLeaderTrade();
      }
      break;
    default:
      break;
  }
}

function ensureSocket(token: string | null) {
  if (sharedWs && sharedToken === token) return;
  sharedWs?.close();
  sharedToken = token;
  const url = token ? `${WS_BASE}/ws/feed?token=${token}` : `${WS_BASE}/ws/feed`;
  const ws = new ReconnectingWebSocket(url, {
    onMessage: dispatchMessage,
    onOpen: () => {
      desiredSubs.forEach((sub) => ws.send({ subscribe: sub.channel, ...sub.params }));
    },
  });
  ws.connect();
  sharedWs = ws;
}

export function useWebSocket() {
  const token = useAuthStore((s) => s.token);

  useEffect(() => {
    refCount++;
    ensureSocket(token ?? null);
    return () => {
      refCount--;
      if (refCount <= 0) {
        sharedWs?.close();
        sharedWs = null;
        sharedToken = undefined;
      }
    };
  }, [token]);

  const subscribe = useCallback((channel: string, params?: Record<string, any>) => {
    desiredSubs.set(channel, { channel, params });
    sharedWs?.send({ subscribe: channel, ...params });
  }, []);

  const unsubscribe = useCallback((channel: string) => {
    // Deliberately does NOT send an unsubscribe upstream: the socket is shared
    // and another consumer may still rely on the channel. The map keeps it for
    // resubscribe-on-reconnect; channels are cheap (backend filters by set).
    void channel;
  }, []);

  return { subscribe, unsubscribe };
}
