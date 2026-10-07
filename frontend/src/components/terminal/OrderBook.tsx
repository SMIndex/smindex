import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatCompact } from '@/lib/formatters';
import { useMarketStore } from '@/stores/marketStore';
import TradesTape from './TradesTape';

type Tab = 'book' | 'trades' | 'funding';

const fmtSize = (s: number) => (s >= 1000 ? s.toFixed(0) : s >= 1 ? s.toFixed(2) : s.toFixed(4));

function OrderBookView({ marketId }: { marketId: number }) {
  const setLimitPrice = useTradingStore((s) => s.setLimitPrice);
  const market = useMarketStore((s) => s.markets[marketId]);
  const config = MARKETS[marketId];
  const decimals = config?.decimals ?? 2;

  // Live L2 book streamed from Perpl (fed by useMarketDataWs mounted in
  // Trade.tsx). The old REST /api/orderbook cache walks the on-chain book
  // from price 0 and can no longer reach the spread through dust levels —
  // it must NOT be used for display (its prices are thousands off).
  const liveBook = useTradingStore((s) => s.orderBook);

  const bids = liveBook.bids;
  const asks = liveBook.asks;

  let askCum = 0;
  const asksDisplay = [...asks].slice(0, 12).map((l: any) => { askCum += l.size; return { ...l, cum: askCum }; });
  let bidCum = 0;
  const bidsDisplay = bids.slice(0, 12).map((l: any) => { bidCum += l.size; return { ...l, cum: bidCum }; });
  const maxCum = Math.max(askCum, bidCum, 0.0001);

  const spread = asks.length && bids.length ? asks[0].price - bids[0].price : 0;
  const spreadPct = bids.length && bids[0].price > 0 ? (spread / bids[0].price) * 100 : 0;
  const up = (market?.price_change_24h ?? 0) >= 0;

  const Row = ({ l, side }: { l: any; side: 'ask' | 'bid' }) => (
    <div onClick={() => setLimitPrice(l.price)}
      className="grid items-center px-4 py-[3.5px] cursor-pointer relative transition-colors hover:bg-[var(--surface-2)]"
      style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
      <div className="absolute top-px bottom-px right-0 rounded-l-[3px]"
        style={{ width: `${(l.cum / maxCum) * 100}%`, opacity: 0.12, background: side === 'ask' ? 'var(--red)' : 'var(--green)' }} />
      <span className="relative z-10 font-semibold" style={{ color: side === 'ask' ? 'var(--red)' : 'var(--green)' }}>{formatPrice(l.price, decimals)}</span>
      <span className="relative z-10 text-right" style={{ color: 'var(--text)' }}>{fmtSize(l.size)}</span>
      <span className="relative z-10 text-right" style={{ color: 'var(--dim)' }}>{fmtSize(l.cum)}</span>
    </div>
  );

  return (
    <>
      {/* Column header */}
      <div className="grid px-4 pt-2.5 pb-1.5 text-[9.5px] font-bold uppercase tracking-[0.5px] rd-sans" style={{ gridTemplateColumns: '1fr 1fr 1fr', color: 'var(--faint)' }}>
        <span>Price</span><span className="text-right">Size</span><span className="text-right">Total</span>
      </div>

      {/* Asks (above mid) */}
      <div className="flex-1 overflow-hidden flex flex-col justify-end rd-mono text-[11.5px]">
        {[...asksDisplay].reverse().map((l, i) => <Row key={`a${i}`} l={l} side="ask" />)}
      </div>

      {/* Mid row */}
      <div className="flex items-center justify-between px-4 py-[11px]" style={{ background: 'var(--surface-2)', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
        <span className="rd-mono font-extrabold text-[16px]" style={{ color: up ? 'var(--green)' : 'var(--red)' }}>
          {market ? `$${formatPrice(market.mark_price, decimals)}` : '--'}
        </span>
        <span className="rd-mono text-[10.5px]" style={{ color: 'var(--faint)' }}>
          Spread {formatPrice(spread, decimals)} ({spreadPct.toFixed(2)}%)
        </span>
      </div>

      {/* Bids (below mid) */}
      <div className="flex-1 overflow-hidden rd-mono text-[11.5px]">
        {bidsDisplay.map((l, i) => <Row key={`b${i}`} l={l} side="bid" />)}
      </div>
    </>
  );
}

function FundingRatesView({ marketId }: { marketId: number }) {
  const { data: compareData } = useQuery({
    queryKey: ['funding-comparison'],
    queryFn: () => fetch('/api/funding-compare').then((r) => r.json()),
    refetchInterval: 60000,
  });

  const { data: hlBook } = useQuery({
    queryKey: ['hl-orderbook', MARKETS[marketId]?.symbol],
    queryFn: () => fetch(`/api/funding-compare/orderbook/${MARKETS[marketId]?.symbol}`).then((r) => r.json()),
    enabled: !!MARKETS[marketId]?.symbol,
    refetchInterval: 30000,
  });

  return (
    <div className="overflow-y-auto rd-mono text-[10px]">
      <div className="px-3 py-1.5 text-[9px] uppercase tracking-wider font-bold rd-sans" style={{ color: 'var(--faint)', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
        Funding Rates
      </div>
      <table className="w-full">
        <thead>
          <tr style={{ color: 'var(--faint)' }}>
            <th className="px-3 py-1 text-left font-semibold rd-sans">Market</th>
            <th className="px-3 py-1 text-right font-semibold rd-sans">Perpl</th>
            <th className="px-3 py-1 text-right font-semibold rd-sans">HL</th>
          </tr>
        </thead>
        <tbody>
          {(compareData || []).map((m: any) => {
            const perplFr = m.perpl?.funding_rate || 0;
            const hlFr = m.hyperliquid?.funding_rate;
            return (
              <tr key={m.symbol} className={clsx(m.symbol === MARKETS[marketId]?.symbol && 'bg-accent/5')} style={{ borderTop: '1px solid var(--border)' }}>
                <td className="px-3 py-1.5 font-bold" style={{ color: 'var(--text)' }}>{m.symbol}</td>
                <td className="px-3 py-1.5 text-right font-medium tabular-nums" style={{ color: perplFr > 0 ? 'var(--red)' : perplFr < 0 ? 'var(--green)' : 'var(--dim)' }}>{(perplFr * 100).toFixed(3)}%</td>
                <td className="px-3 py-1.5 text-right font-medium tabular-nums" style={{ color: hlFr && hlFr > 0 ? 'var(--red)' : hlFr && hlFr < 0 ? 'var(--green)' : 'var(--dim)' }}>
                  {hlFr !== null && hlFr !== undefined ? `${(hlFr * 100).toFixed(4)}%` : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="px-3 py-1.5 mt-2 text-[9px] uppercase tracking-wider font-bold rd-sans" style={{ color: 'var(--faint)', background: 'var(--surface-2)', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}>
        HL Liquidity — {MARKETS[marketId]?.symbol}
      </div>
      {hlBook?.whales?.length ? (
        <div>
          {hlBook.whales.slice(0, 10).map((w: any, i: number) => (
            <div key={i} className="flex items-center justify-between px-3 py-1" style={{ borderTop: '1px solid var(--border)' }}>
              <span className="font-semibold" style={{ color: w.side === 'bid' ? 'var(--green)' : 'var(--red)' }}>{w.side === 'bid' ? 'BID' : 'ASK'}</span>
              <span className="tabular-nums" style={{ color: 'var(--text)' }}>${formatPrice(w.price, MARKETS[marketId]?.decimals ?? 2)}</span>
              <span className="tabular-nums" style={{ color: 'var(--dim)' }}>${formatCompact(w.notional)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-3 py-4 text-center" style={{ color: 'var(--faint)' }}>Loading HL data…</div>
      )}
    </div>
  );
}

export default function OrderBook({ marketId }: { marketId: number }) {
  const [tab, setTab] = useState<Tab>('book');
  return (
    <div className="flex flex-col h-full">
      {/* Tabs */}
      <div className="flex gap-1 px-3.5 py-3 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        {([['book', 'Order Book'], ['trades', 'Trades'], ['funding', 'Funding']] as [Tab, string][]).map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className="px-3 py-1.5 rounded-[8px] text-[12px] font-semibold rd-sans transition-colors"
            style={tab === t ? { background: 'var(--surface-2)', color: 'var(--text)' } : { color: 'var(--dim)' }}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'book' ? <OrderBookView marketId={marketId} />
        : tab === 'trades' ? <TradesTape marketId={marketId} />
        : <FundingRatesView marketId={marketId} />}
    </div>
  );
}
