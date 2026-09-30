import { useEffect } from 'react';
import { WS_BASE } from '@/config/constants';
import { useTradingStore } from '@/stores/tradingStore';
import { useMarketStore } from '@/stores/marketStore';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { candleBus } from '@/lib/candleBus';

const WS_URL = `${WS_BASE}/ws/market-data`;
const CHAIN_ID = 143;

// Live market data from Perpl's market-data WS, relayed verbatim by the backend
// proxy. One socket per Trade page, carrying (verified against the real stream
// 2026-08-07):
//   order-book@<mkt>   mt:15 snapshot / mt:16 delta  {bid:[{p,s,o}], ask:[...]}
//   market-state@143   mt:9  {d: {"<mkt>": {orl,mrk,lst,mid,bid,ask,prv,dv,dva,oi,tvl}}}
//   funding@143        mt:10 {d: {"<mkt>": {rate,...}}}   rate/10000 = decimal
//   candles@<mkt>*<res> mt:11 snapshot / mt:12 delta {r, d:[{t,o,c,h,l,v,n}]}
//   trades@<mkt>       mt:17 snapshot / mt:18 delta {d:[{at:{t},p,s,sd}]} sd 1=buy 2=sell
//   heartbeat@143      mt:100 {sn} — block number, gap => resubscribe
// This is what makes the ticker/candles/trades update at block cadence like the
// Perpl app; the backend's 3s REST poll (/ws/feed) remains as fallback/seed.
export function useMarketDataWs(marketId: number) {
  const setOrderBook = useTradingStore((s) => s.setOrderBook);
  const addTrades = useTradingStore((s) => s.addTrades);
  const resolution = useTradingStore((s) => s.resolution);

  useEffect(() => {
    let closed = false;              // set on unmount so reconnect stops
    let ws: WebSocket | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;             // exponential, capped at 15s
    let lastHbSn: number | null = null;
    let haveSnapshot = false;
    let haveTradesSnapshot = false;
    // Raw books: scaled-int price -> scaled-int size. Kept raw so a snapshot
    // arriving before market configs load is never thrown away.
    const bidMap = new Map<number, number>();
    const askMap = new Map<number, number>();

    const subscribe = () => {
      haveSnapshot = false;
      haveTradesSnapshot = false;
      try {
        ws?.send(JSON.stringify({
          mt: 5,
          subs: [
            { stream: `order-book@${marketId}`, subscribe: true },
            { stream: `candles@${marketId}*${resolution}`, subscribe: true },
            { stream: `trades@${marketId}`, subscribe: true },
            { stream: `market-state@${CHAIN_ID}`, subscribe: true },
            { stream: `funding@${CHAIN_ID}`, subscribe: true },
            { stream: `heartbeat@${CHAIN_ID}`, subscribe: true },
          ],
        }));
      } catch {}
    };

    const publish = () => {
      const mcfg = MARKET_CONFIGS[marketId];
      if (!mcfg) return; // configs still loading; raw maps keep the state
      const pd = 10 ** mcfg.priceDecimals;
      const sd = 10 ** mcfg.sizeDecimals;
      const bids = [...bidMap.entries()].sort((x, y) => y[0] - x[0])
        .map(([p, s]) => ({ price: p / pd, size: s / sd }));
      const asks = [...askMap.entries()].sort((x, y) => x[0] - y[0])
        .map(([p, s]) => ({ price: p / pd, size: s / sd }));
      setOrderBook(bids, asks);
    };

    const applyLevels = (map: Map<number, number>, levels: any[]) => {
      for (const l of levels || []) {
        if (!l || typeof l.p !== 'number') continue;
        if (!l.s || !l.o) map.delete(l.p);
        else map.set(l.p, l.s);
      }
    };

    // mt:9 market-state — same field mapping the backend 3s poll uses
    // (ws_manager.py), so both writers produce identical store shapes.
    const applyMarketState = (d: Record<string, any>) => {
      const update = useMarketStore.getState().updateMarket;
      for (const [idStr, st] of Object.entries(d || {})) {
        const id = Number(idStr);
        const mcfg = MARKET_CONFIGS[id];
        if (!mcfg || !st) continue;
        const pd = 10 ** mcfg.priceDecimals;
        const sd = 10 ** mcfg.sizeDecimals;
        const mark = (st.mrk ?? 0) / pd;
        const prev = (st.prv ?? 0) / pd;
        const oi = (st.oi ?? 0) / sd;
        const patch: Record<string, number> = {
          mark_price: mark,
          last_price: (st.lst ?? 0) / pd,
          oracle_price: (st.orl ?? 0) / pd,
          mid_price: (st.mid ?? 0) / pd,
          bid_price: (st.bid ?? 0) / pd,
          ask_price: (st.ask ?? 0) / pd,
          prev_price: prev,
          open_interest: oi,
          open_interest_usd: oi * mark,
          daily_volume: (st.dv ?? 0) / sd,
        };
        // Only include fields we actually have — a spread with `undefined`
        // values would wipe what the 3s poll already populated.
        if (st.dva) patch.daily_volume_usd = Number(st.dva) / 1e6;
        if (st.tvl) patch.tvl = Number(st.tvl) / 1e6;
        if (prev) patch.price_change_24h = ((mark - prev) / prev) * 100;
        update(id, patch as any);
      }
    };

    const applyFunding = (d: Record<string, any>) => {
      const update = useMarketStore.getState().updateMarket;
      for (const [idStr, f] of Object.entries(d || {})) {
        if (!f || typeof f.rate !== 'number') continue;
        // funding@143 includes delisted/unlisted ids (30,60,70,80 live-verified
        // 2026-08-25) — without the same config guard applyMarketState uses,
        // these create phantom "#30 $NaN" rows in every market selector.
        if (!MARKET_CONFIGS[Number(idStr)]) continue;
        update(Number(idStr), { funding_rate: f.rate / 10000 } as any);
      }
    };

    const mapTrades = (rows: any[], pd: number, sd: number) =>
      (rows || [])
        .filter((tr) => tr && typeof tr.p === 'number')
        .map((tr) => ({
          price: tr.p / pd,
          size: (tr.s ?? 0) / sd,
          side: (tr.sd === 2 ? 'sell' : 'buy') as 'buy' | 'sell',
          time: Math.floor((tr.at?.t ?? Date.now()) / 1000),
        }));

    const scheduleReconnect = () => {
      if (closed || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, backoff);
      backoff = Math.min(backoff * 2, 15000);
    };

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        backoff = 1000;             // reset backoff on a good connect
        lastHbSn = null;
        subscribe();
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          try {
            if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ mt: 1, t: Date.now() }));
          } catch {}
        }, 25000);
      };

      ws.onclose = () => { if (pingTimer) { clearInterval(pingTimer); pingTimer = null; } scheduleReconnect(); };
      ws.onerror = () => { try { ws?.close(); } catch {} };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const msg = JSON.parse(event.data);

          if (msg.mt === 100) {
            // Heartbeat sn = block number, +1 per block. A gap means we may
            // have missed deltas — resubscribe for fresh snapshots.
            const sn = msg.sn;
            if (typeof sn === 'number') {
              if (lastHbSn !== null && sn !== lastHbSn + 1) subscribe();
              lastHbSn = sn;
            }
            return;
          }

          if (msg.mt === 15) {
            // Snapshot: replace the whole book
            bidMap.clear();
            askMap.clear();
            applyLevels(bidMap, msg.bid);
            applyLevels(askMap, msg.ask);
            haveSnapshot = true;
            publish();
            return;
          }

          if (msg.mt === 16) {
            // Delta before any snapshot (e.g. right after a resubscribe race):
            // we can't apply it safely — ask for a fresh snapshot instead.
            if (!haveSnapshot) { subscribe(); return; }
            applyLevels(bidMap, msg.bid);
            applyLevels(askMap, msg.ask);
            publish();
            return;
          }

          if (msg.mt === 9 && msg.d) { applyMarketState(msg.d); return; }
          if (msg.mt === 10 && msg.d) { applyFunding(msg.d); return; }

          if ((msg.mt === 11 || msg.mt === 12) && Array.isArray(msg.d) && msg.r === resolution) {
            // Snapshot: only the tail matters (chart history comes via REST) —
            // update: prior closed candle + live candle.
            const tail = msg.mt === 11 ? msg.d.slice(-2) : msg.d;
            if (tail.length) candleBus.emit({ marketId, resolution, candles: tail });
            return;
          }

          if (msg.mt === 17 || msg.mt === 18) {
            const mcfg = MARKET_CONFIGS[marketId];
            if (!mcfg) return;
            const pd = 10 ** mcfg.priceDecimals;
            const sd = 10 ** mcfg.sizeDecimals;
            const rows = mapTrades(msg.d, pd, sd);
            if (!rows.length) return;
            if (msg.mt === 17) {
              // Snapshot arrives oldest→newest; the tape shows newest first.
              // REPLACE (not prepend) so a gap-triggered resubscribe never
              // duplicates rows already on the tape.
              haveTradesSnapshot = true;
              useTradingStore.setState({ recentTrades: rows.slice(-50).reverse() });
            } else if (haveTradesSnapshot) {
              addTrades(rows.reverse());
            }
            return;
          }
        } catch {
          // ignore parse errors
        }
      };
    };

    connect();

    return () => {
      closed = true;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { ws?.close(); } catch {}
      setOrderBook([], []); // don't leave a stale book behind on market switch
    };
  }, [marketId, resolution, setOrderBook, addTrades]);
}
