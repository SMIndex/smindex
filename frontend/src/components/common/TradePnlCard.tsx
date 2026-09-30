import { useRef, useState } from 'react';
import { clsx } from 'clsx';
import { formatUSD, formatPrice, shortenAddress } from '@/lib/formatters';
import { useAuthStore } from '@/stores/authStore';

interface TradePnlCardProps {
  trade: {
    symbol: string;
    direction?: string;
    side?: string;
    action?: string;
    price: number;
    size: number;
    notional?: number;
    order_value?: number;
    fee?: number;
    pnl: number;
    leverage?: number;
    time?: string;
    source?: string;
    order_type?: string;
  };
  onClose: () => void;
}

export default function TradePnlCard({ trade, onClose }: TradePnlCardProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const user = useAuthStore((s) => s.user);

  const direction = trade.direction || `${trade.action === 'close' ? 'Close' : 'Open'} ${trade.side === 'long' ? 'Long' : 'Short'}`;
  const isLong = direction.includes('Long');
  const isProfit = trade.pnl >= 0;
  const value = trade.notional || trade.order_value || (trade.size * trade.price);
  const roe = value > 0 ? (trade.pnl / value) * 100 * (trade.leverage || 1) : 0;

  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    if (!cardRef.current || downloading) return;
    setDownloading(true);
    try {
      const html2canvas = (await import('html2canvas')).default;
      const canvas = await html2canvas(cardRef.current, {
        backgroundColor: '#0a0a1a',
        scale: 2,
        useCORS: true,
        logging: false,
      });

      const dataUrl = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.download = `perpl-${trade.symbol}-${isProfit ? 'profit' : 'loss'}.png`;
      link.href = dataUrl;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (err) {
      console.error('PnL card download failed:', err);
      // Text fallback
      const text = `${trade.symbol} ${direction}\nPnL: ${isProfit ? '+' : ''}${formatUSD(trade.pnl)}\nPrice: $${trade.price}\nSize: ${trade.size}\nsmindex.xyz`;
      try { await navigator.clipboard.writeText(text); } catch {}
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div className="max-w-[360px] w-full" onClick={(e) => e.stopPropagation()}>
        {/* Card — captured as image */}
        <div
          ref={cardRef}
          className="rounded-2xl overflow-hidden"
          style={{
            background: isProfit
              ? 'linear-gradient(135deg, #0a1f0a 0%, #0f2d1a 40%, #1a3a2a 100%)'
              : 'linear-gradient(135deg, #1f0a0a 0%, #2d0f0f 40%, #3a1a1a 100%)',
          }}
        >
          {/* Header */}
          <div className="px-5 pt-5 pb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 rounded-lg bg-white/10 flex items-center justify-center">
                <span className="text-xs font-bold text-white/80">P</span>
              </div>
              <div>
                <div className="text-xs font-bold text-white/90">SMINDEX</div>
                <div className="text-[9px] text-white/40">
                  {user?.username || shortenAddress(user?.wallet_address || '')}
                </div>
              </div>
            </div>
            <div className="text-[9px] text-white/30">smindex.xyz</div>
          </div>

          {/* Coin + Direction */}
          <div className="px-5 pt-3 pb-1">
            <div className="flex items-center gap-2">
              <span className="text-xl font-bold text-white">{trade.symbol}</span>
              <span className={clsx(
                'text-xs font-bold px-2 py-0.5 rounded',
                isLong ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400',
              )}>
                {direction}
              </span>
              {trade.source === 'copy_trade' && (
                <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-400 font-medium">
                  Social Trade
                </span>
              )}
            </div>
          </div>

          {/* PnL */}
          <div className="px-5 py-3">
            <div className="text-[10px] text-white/40 mb-0.5">Realized PnL</div>
            <div className={clsx('text-4xl font-black tracking-tight', isProfit ? 'text-emerald-400' : 'text-red-400')}>
              {isProfit ? '+' : ''}{formatUSD(trade.pnl)}
            </div>
            {roe !== 0 && (
              <div className={clsx('text-sm font-bold mt-0.5', isProfit ? 'text-emerald-400/70' : 'text-red-400/70')}>
                {isProfit ? '+' : ''}{roe.toFixed(1)}% ROE
              </div>
            )}
          </div>

          {/* Trade Details */}
          <div className="px-5 pb-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              <div>
                <div className="text-[9px] text-white/30">Price</div>
                <div className="text-xs font-semibold text-white/80">${formatPrice(trade.price, 6)}</div>
              </div>
              <div>
                <div className="text-[9px] text-white/30">Size</div>
                <div className="text-xs font-semibold text-white/80">{trade.size.toLocaleString()}</div>
              </div>
              <div>
                <div className="text-[9px] text-white/30">Trade Value</div>
                <div className="text-xs font-semibold text-white/80">{formatUSD(value)}</div>
              </div>
              <div>
                <div className="text-[9px] text-white/30">Fee</div>
                <div className="text-xs font-semibold text-white/80">{trade.fee != null ? formatUSD(trade.fee) : '$0.00'}</div>
              </div>
              {trade.leverage && (
                <div>
                  <div className="text-[9px] text-white/30">Leverage</div>
                  <div className="text-xs font-semibold text-white/80">{trade.leverage}x</div>
                </div>
              )}
              {trade.order_type && (
                <div>
                  <div className="text-[9px] text-white/30">Type</div>
                  <div className="text-xs font-semibold text-white/80 capitalize">{trade.order_type}</div>
                </div>
              )}
            </div>
          </div>

          {/* Footer */}
          <div className="px-5 pb-4 flex items-center justify-between">
            <div className="text-[9px] text-white/25">
              {trade.time ? new Date(trade.time).toLocaleString() : ''}
            </div>
            <div className="text-[9px] text-white/25">Monad Mainnet</div>
          </div>
        </div>

        {/* Buttons */}
        <div className="flex gap-2 mt-3">
          <button onClick={handleDownload} disabled={downloading} className="flex-1 btn-primary text-sm py-2.5 font-semibold">
            {downloading ? 'Generating...' : 'Download Image'}
          </button>
          <button onClick={onClose} className="px-5 py-2.5 text-sm text-text-secondary hover:text-text-primary bg-bg-secondary rounded-lg">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
