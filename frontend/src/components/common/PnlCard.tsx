import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getPnlCard } from '@/lib/api';
import { formatUSD, shortenAddress } from '@/lib/formatters';
import { useAuth } from '@/hooks/useAuth';
import { useAuthStore } from '@/stores/authStore';
import LoadingSpinner from './LoadingSpinner';

export default function PnlCard({ onClose }: { onClose: () => void }) {
  const { address } = useAuth();
  const user = useAuthStore((s) => s.user);
  const cardRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['pnl-card', address],
    queryFn: getPnlCard,
    enabled: !!address,
  });

  const [downloading, setDownloading] = useState(false);

  const handleShare = async () => {
    if (!cardRef.current || downloading) return;
    setDownloading(true);
    try {
      const html2canvas = (await import('html2canvas')).default;
      const canvas = await html2canvas(cardRef.current, {
        backgroundColor: '#0f0f1a',
        scale: 2,
        useCORS: true,
        logging: false,
      });
      const dataUrl = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.download = 'perpl-pnl.png';
      link.href = dataUrl;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (err) {
      console.error('PnL card download failed:', err);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="max-w-sm w-full" onClick={(e) => e.stopPropagation()}>
        {isLoading ? (
          <div className="card flex justify-center py-12"><LoadingSpinner /></div>
        ) : !data || data.total_trades === 0 ? (
          <div className="card text-center py-8">
            <p className="text-sm text-text-secondary">No trades yet to generate a PnL card.</p>
            <button onClick={onClose} className="mt-4 text-xs text-accent hover:underline">Close</button>
          </div>
        ) : (
          <>
            {/* The card itself — this is what gets captured as image */}
            <div ref={cardRef} className="rounded-2xl overflow-hidden" style={{ background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)' }}>
              {/* Header */}
              <div className="px-5 pt-5 pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-8 h-8 rounded-lg bg-accent/20 flex items-center justify-center">
                      <span className="text-sm font-bold text-accent">P</span>
                    </div>
                    <div>
                      <div className="text-sm font-bold text-white">SMINDEX</div>
                      <div className="text-[10px] text-gray-400">
                        {user?.username ? user.username : shortenAddress(data.wallet_address)}
                      </div>
                    </div>
                  </div>
                  <div className="text-[10px] text-gray-500">smindex.xyz</div>
                </div>
              </div>

              {/* Main PnL */}
              <div className="px-5 py-4">
                <div className="text-xs text-gray-400 mb-1">Total Realized PnL</div>
                <div className={clsx('text-3xl font-bold', data.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                  {data.total_pnl >= 0 ? '+' : ''}{formatUSD(data.total_pnl)}
                </div>
                {data.total_fees > 0 && (
                  <div className="text-xs text-gray-500 mt-1">
                    Net after fees: <span className={clsx('font-semibold', (data.total_pnl - data.total_fees) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {(data.total_pnl - data.total_fees) >= 0 ? '+' : ''}{formatUSD(data.total_pnl - data.total_fees)}
                    </span>
                  </div>
                )}
                {data.first_trade && data.last_trade && (
                  <div className="text-[10px] text-gray-600 mt-1">
                    {new Date(data.first_trade).toLocaleDateString()} — {new Date(data.last_trade).toLocaleDateString()}
                  </div>
                )}
              </div>

              {/* Stats grid */}
              <div className="px-5 pb-4 grid grid-cols-3 gap-3">
                <div>
                  <div className="text-[10px] text-gray-500">Trades</div>
                  <div className="text-sm font-bold text-white">{data.total_trades}</div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-500">Win Rate</div>
                  <div className={clsx('text-sm font-bold', data.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                    {data.win_rate}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-500">Volume</div>
                  <div className="text-sm font-bold text-white">{formatUSD(data.total_volume)}</div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-500">Wins</div>
                  <div className="text-sm font-bold text-emerald-400">{data.wins}</div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-500">Losses</div>
                  <div className="text-sm font-bold text-red-400">{data.losses}</div>
                </div>
                <div>
                  <div className="text-[10px] text-gray-500">Fees</div>
                  <div className="text-sm font-bold text-gray-300">{formatUSD(data.total_fees)}</div>
                </div>
              </div>

              {/* Best/Worst trade */}
              {(data.best_trade || data.worst_trade) && (
                <div className="px-5 pb-4 flex gap-3">
                  {data.best_trade && (
                    <div className="flex-1 bg-emerald-500/10 rounded-lg px-3 py-2">
                      <div className="text-[10px] text-emerald-400/70">Best Trade</div>
                      <div className="text-xs font-bold text-emerald-400">
                        {data.best_trade.symbol} {data.best_trade.side} +{formatUSD(data.best_trade.pnl)}
                      </div>
                    </div>
                  )}
                  {data.worst_trade && (
                    <div className="flex-1 bg-red-500/10 rounded-lg px-3 py-2">
                      <div className="text-[10px] text-red-400/70">Worst Trade</div>
                      <div className="text-xs font-bold text-red-400">
                        {data.worst_trade.symbol} {data.worst_trade.side} {formatUSD(data.worst_trade.pnl)}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Per-market breakdown */}
              {data.by_market && Object.keys(data.by_market).length > 0 && (
                <div className="px-5 pb-5">
                  <div className="text-[10px] text-gray-500 mb-2">By Market</div>
                  <div className="flex gap-2">
                    {Object.entries(data.by_market).map(([sym, stats]: [string, any]) => (
                      <div key={sym} className="flex-1 bg-white/5 rounded-lg px-2.5 py-2 text-center">
                        <div className="text-[10px] font-bold text-white">{sym}</div>
                        <div className={clsx('text-[10px] font-bold', stats.pnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                          {stats.pnl >= 0 ? '+' : ''}{formatUSD(stats.pnl)}
                        </div>
                        <div className="text-[9px] text-gray-500">{stats.trades} trades</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Actions (outside the captured card) */}
            <div className="flex gap-2 mt-3">
              <button
                onClick={handleShare}
                className="flex-1 btn-primary text-sm py-2"
              >
                {downloading ? 'Generating...' : 'Download Image'}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary bg-bg-secondary rounded-lg"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
