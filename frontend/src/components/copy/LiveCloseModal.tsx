import { useEffect, useState } from 'react';
import { useAccount } from 'wagmi';
import { clsx } from 'clsx';
import Modal from '@/components/common/Modal';
import { useCopyTrade } from '@/hooks/useCopyTrade';
import { useMarketStore } from '@/stores/marketStore';
import { useToast } from '@/components/common/Toast';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { createCopyOrder, updateCopyOrder, copyOrderReasonLabel, getLiveCopyStatus, apiErrorMessage, type LivePosition, type LiveCopyStatus } from '@/lib/copyApi';
import { formatUSD, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  position: LivePosition;
  onClosed?: () => void;
}

// Live close/reduce: places a REAL Perpl close order via the same client wallet-signed
// path the terminal uses, ONLY after the user confirms AND the backend authorizes.
export default function LiveCloseModal({ isOpen, onClose, position, onClosed }: Props) {
  const { address } = useAccount();
  const { closeLivePosition, isCopying, status } = useCopyTrade();
  const toast = useToast();
  const liveMarket = useMarketStore((s) => s.markets[position.follower_market_id]);
  const mcfg = MARKET_CONFIGS[position.follower_market_id];
  const configMissing = !mcfg || mcfg.priceDecimals == null || mcfg.sizeDecimals == null;
  const priceDec = mcfg?.priceDecimals ?? 2;

  const [pct, setPct] = useState(100);
  const [blocked, setBlocked] = useState<string | null>(null);
  // RUNTIME availability, same as LiveCopyModal (the old build-time
  // VITE_COPY_LIVE_ENABLED flag froze this modal disabled in any bundle built
  // without the env var — found live during the Part B protocol: the user
  // could OPEN a position but not close it. Server truth, not a build flag.
  const [liveStatus, setLiveStatus] = useState<LiveCopyStatus | null>(null);
  useEffect(() => {
    if (isOpen) getLiveCopyStatus().then(setLiveStatus).catch(() => setLiveStatus(null));
  }, [isOpen]);
  const liveAvailable = !!liveStatus?.live_available;

  const currentSize = position.current_size ?? position.entry_size ?? 0;
  const markPrice = liveMarket?.mark_price && liveMarket.mark_price > 0 ? liveMarket.mark_price : (position.entry_price ?? 0);
  const closeSize = Math.max(0, currentSize * (pct / 100));
  const isFull = pct >= 100;
  const action: 'close' | 'reduce' = isFull ? 'close' : 'reduce';
  const canSubmit = liveAvailable && !configMissing && closeSize > 0 && markPrice > 0 && !isCopying;

  const handleClose = async () => {
    if (!canSubmit) return;
    setBlocked(null);
    if (configMissing) { setBlocked('Market config unavailable'); return; }
    if (!address) { toast.error('Connect your wallet first'); return; }

    // 1) Record the confirmed close/reduce attempt + backend gate (owner, live, dup).
    const idempotency_key = `${action}:${address.toLowerCase()}:pos:${position.id}:${Date.now()}`;
    let order;
    try {
      order = await createCopyOrder({
        trader_wallet: position.trader_wallet,
        market_id: position.follower_market_id,
        symbol: position.symbol,
        side: position.side,
        intended_size: closeSize,
        intended_price: markPrice,
        leverage: Math.min(Math.max(1, Math.round(position.entry_leverage ?? 1)), 20),
        allocation_usd: position.entry_margin ?? 1,
        idempotency_key,
        action,
        live_position_id: position.id,
      });
    } catch (e: any) {
      toast.error(apiErrorMessage(e) || 'Could not start close');
      return;
    }
    if (order.status !== 'submitted') {
      const reason = copyOrderReasonLabel(order.skip_reason, order.status);
      setBlocked(reason); toast.error(reason); return;
    }

    // 2) Place the real Perpl close order, then report the result.
    try {
      const result: any = await closeLivePosition({
        marketId: position.follower_market_id, side: position.side,
        size: closeSize, markPrice,
      });
      const fillPrice = result?.filledPrice ? result.filledPrice / (10 ** (mcfg?.priceDecimals ?? 2)) : undefined;
      const fillSize = result?.filledSize ? result.filledSize / (10 ** (mcfg?.sizeDecimals ?? 2)) : closeSize;
      await updateCopyOrder(order.id, {
        status: 'filled',
        perpl_order_id: result?.orderId != null ? String(result.orderId) : null,
        fill_price: fillPrice ?? markPrice,
        fill_size: fillSize ?? null,
      }).catch(() => {});
      toast.success(`${isFull ? 'Closed' : 'Reduced'} ${position.symbol}`);
      onClosed?.();
      onClose();
    } catch (e: any) {
      await updateCopyOrder(order.id, { status: 'failed', error_message: e?.message || 'Close failed' }).catch(() => {});
      // closeLivePosition already surfaced an error toast where relevant
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={isFull ? 'Close Copied Position' : 'Reduce Copied Position'} maxWidth="max-w-md">
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg bg-bg-secondary/60 border border-text-secondary/10">
          <div>
            <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Position</div>
            <div className="text-sm font-semibold text-text-primary">
              {position.symbol} <span className={clsx('uppercase', position.side === 'long' ? 'text-success' : 'text-danger')}>{position.side}</span>
            </div>
          </div>
          <span className="text-[10px] font-medium px-2 py-1 rounded-full bg-danger/10 text-danger border border-danger/20">LIVE</span>
        </div>

        {position.close_suggested && (
          <div className="px-3 py-2 rounded-lg bg-warning/10 border border-warning/25 text-[11px] text-text-secondary">
            Leader {position.close_suggestion_type === 'reduce' ? 'reduced' : 'closed'} this position — confirm to {position.close_suggestion_type === 'reduce' ? 'reduce' : 'close'} yours.
          </div>
        )}

        <div className="p-3 bg-bg-secondary rounded-lg grid grid-cols-3 gap-2 text-xs">
          <div><span className="text-text-secondary/60">Size</span><div className="font-bold text-text-primary tabular-nums">{formatSize(currentSize)}</div></div>
          <div><span className="text-text-secondary/60">Entry</span><div className="font-bold text-text-primary tabular-nums">${formatPrice(position.entry_price ?? 0, priceDec)}</div></div>
          <div><span className="text-text-secondary/60">Mark</span><div className="font-bold text-text-primary tabular-nums">${formatPrice(markPrice, priceDec)}</div></div>
        </div>

        {/* Amount */}
        <div>
          <div className="flex justify-between text-xs mb-1.5">
            <label className="font-medium text-text-primary">{isFull ? 'Close all' : 'Reduce'}</label>
            <span className="text-accent font-bold">{pct}% ({formatSize(closeSize)} {position.symbol})</span>
          </div>
          <input type="range" min={10} max={100} step={10} value={pct} onChange={(e) => setPct(Number(e.target.value))} className="w-full accent-accent" />
        </div>

        <div className="p-3 bg-danger/5 border border-danger/20 rounded-lg">
          <p className="text-xs text-text-secondary">
            <span className="text-danger font-semibold">This will place a real Perpl {isFull ? 'close' : 'reduce'} order</span> on your position.
          </p>
        </div>

        {configMissing && <div className="p-3 bg-danger/5 border border-danger/20 rounded-lg text-xs text-danger">Market config unavailable — cannot size this order.</div>}
        {liveStatus && !liveAvailable && (
          <div className="p-3 bg-warning/5 border border-warning/20 rounded-lg text-xs text-text-secondary">
            {!liveStatus.authenticated ? 'Sign in with your wallet to close this position.'
              : !liveStatus.live_enabled ? 'Live copy is disabled by the kill switch — no real order will be placed.'
              : 'Your wallet is not in the live-copy allowlist.'}
          </div>
        )}
        {blocked && <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger">{blocked}</div>}
        {status && <div className="text-xs text-accent animate-pulse">{status}</div>}

        <div className="flex items-center gap-2">
          <button onClick={onClose} disabled={isCopying} className="flex-1 text-sm font-medium py-2.5 rounded-lg bg-bg-secondary text-text-secondary hover:text-text-primary transition-colors">Cancel</button>
          <button
            onClick={handleClose}
            disabled={!canSubmit}
            className={clsx('flex-1 text-sm font-semibold py-2.5 rounded-lg transition-colors bg-danger text-white hover:bg-danger/80', !canSubmit && 'opacity-50 cursor-not-allowed')}
          >
            {!liveAvailable ? (liveStatus ? 'Live Copy Unavailable' : 'Checking availability…') : configMissing ? 'Market config unavailable' : isCopying ? 'Placing…' : isFull ? `Confirm Close ${position.symbol}` : `Confirm Reduce ${pct}%`}
          </button>
        </div>
      </div>
    </Modal>
  );
}
