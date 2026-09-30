import { useRef, useEffect } from 'react';
import { clsx } from 'clsx';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKETS } from '@/config/constants';
import { formatPrice } from '@/lib/formatters';

export default function TradesTape({ marketId }: { marketId: number }) {
  const recentTrades = useTradingStore((s) => s.recentTrades);
  const config = MARKETS[marketId];
  const decimals = config?.decimals ?? 2;
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [recentTrades.length]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-2 border-b border-text-secondary/10">
        <div className="text-xs font-semibold text-text-primary">Recent Trades</div>
      </div>

      <div className="flex items-center gap-1 px-3 py-1 text-[10px] text-text-secondary">
        <span className="w-14">Time</span>
        <span className="flex-1">Price</span>
        <span className="w-16 text-right">Size</span>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {recentTrades.length === 0 ? (
          <div className="flex items-center justify-center h-full text-xs text-text-secondary">
            Waiting for trades...
          </div>
        ) : (
          recentTrades.map((t, i) => (
            <div key={i} className="flex items-center gap-1 px-3 py-0.5 text-[11px] font-mono">
              <span className="w-14 text-text-secondary">
                {new Date(t.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
              <span className={clsx('flex-1', t.side === 'buy' ? 'text-success' : 'text-danger')}>
                {formatPrice(t.price, decimals)}
              </span>
              <span className="w-16 text-right text-text-secondary">
                {t.size.toFixed(4)}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
