import { useEffect, useRef, useState } from 'react';
import { getSession, warmupTradingConnection } from '@/lib/perplTrading';

// Phones freeze sockets when locked. On wake (tab visible again) or when the
// network comes back, a trading session that WAS live but whose socket is no
// longer open is re-established, and the shell shows "Reconnecting…" until it
// is (spec 4.6). Uses the existing no-popup reconnect path (API key / stored
// session); never triggers a wallet prompt. The market-data feed socket has
// its own auto-reconnect.
export function useReconnectOnWake(): boolean {
  const [reconnecting, setReconnecting] = useState(false);
  const wasLive = useRef(false);

  useEffect(() => {
    const track = setInterval(() => {
      const s = getSession();
      if (s.authenticated && s.hasWs) wasLive.current = true;
    }, 5000);
    const check = async () => {
      if (document.visibilityState !== 'visible' || !wasLive.current) return;
      if (getSession().hasWs) return;
      setReconnecting(true);
      try {
        await warmupTradingConnection();
      } catch {
        /* the next order click retries through getFreshBlock */
      } finally {
        setReconnecting(false);
      }
    };
    document.addEventListener('visibilitychange', check);
    window.addEventListener('online', check);
    return () => {
      clearInterval(track);
      document.removeEventListener('visibilitychange', check);
      window.removeEventListener('online', check);
    };
  }, []);
  return reconnecting;
}
