// Tiny pub/sub carrying live Perpl candle deltas (WS mt:11/12) from the
// market-data socket to the chart WITHOUT going through React state — the
// chart consumes them imperatively via lightweight-charts series.update().

export interface WsCandle {
  t: number;   // bar open time, ms
  o: number;   // scaled ints, exactly as Perpl sends them (divide by 10^price_decimals)
  h: number;
  l: number;
  c: number;
  v: string;   // scaled volume string (same encoding as the REST candle feed)
  n?: number;  // trade count
}

export interface CandleMsg {
  marketId: number;
  resolution: number;  // seconds
  candles: WsCandle[]; // usually the live candle; on rollover prior closed + new
}

type Listener = (m: CandleMsg) => void;
const listeners = new Set<Listener>();

export const candleBus = {
  emit(m: CandleMsg) {
    listeners.forEach((l) => { try { l(m); } catch {} });
  },
  on(l: Listener): () => void {
    listeners.add(l);
    return () => { listeners.delete(l); };
  },
};
