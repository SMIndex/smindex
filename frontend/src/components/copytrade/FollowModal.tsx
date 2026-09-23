import { useState } from 'react';
import Modal from '@/components/common/Modal';
import { useAuth } from '@/hooks/useAuth';
import { useCopyTrading } from '@/hooks/useCopyTrading';
import { useToast } from '@/components/common/Toast';
import { shortenAddress } from '@/lib/formatters';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import type { Leader } from '@/types/copytrade';

// Real per-market max leverage from Perpl
const MARKET_MAX_LEV: Record<number, { symbol: string; max: number }> = {
  1: { symbol: 'BTC', max: 10 },
  10: { symbol: 'MON', max: 5 },
  20: { symbol: 'ETH', max: 10 },
  30: { symbol: 'SOL', max: 20 },
};

interface FollowModalProps {
  leader: Leader;
  onClose: () => void;
}

export default function FollowModal({ leader, onClose }: FollowModalProps) {
  // Get real max leverage from loaded market configs (fallback to hardcoded)
  const globalMax = Math.max(
    ...Object.values(MARKET_CONFIGS).map((c) => c.maxLeverage),
    ...Object.values(MARKET_MAX_LEV).map((m) => m.max),
  );

  const [allocationUsd, setAllocationUsd] = useState(100);
  const [maxLeverage, setMaxLeverage] = useState(Math.min(10, globalMax));
  const { isAuthenticated, user, login, isLoading: authLoading } = useAuth();
  const { follow, isFollowing } = useCopyTrading();
  const toast = useToast();

  const handleSubmit = async () => {
    if (!isAuthenticated) {
      try {
        await login();
      } catch {
        return;
      }
    }

    try {
      await follow({
        leader_wallet: leader.wallet_address,
        allocation_usd: allocationUsd,
        max_leverage: maxLeverage,
        auto_copy: false,
      });
      toast.success(`Now following ${shortenAddress(leader.wallet_address)}`);
      onClose();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || '';
      if (detail.includes('Already following')) {
        toast.success('Already following this trader');
        onClose();
      } else {
        toast.error(detail || 'Failed to follow leader');
      }
    }
  };

  return (
    <Modal isOpen={true} onClose={onClose} title="Follow Trader">
      <div className="space-y-5">
        {/* Leader info */}
        <div className="flex items-center gap-3 p-3 bg-bg-secondary rounded-lg">
          <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center">
            <span className="text-sm font-bold text-accent">
              {leader.wallet_address[2].toUpperCase()}
            </span>
          </div>
          <div>
            <div className="text-sm font-medium text-text-primary">
              {leader.display_name || shortenAddress(leader.wallet_address)}
            </div>
            <div className="text-xs text-text-secondary font-mono">
              {shortenAddress(leader.wallet_address)}
            </div>
          </div>
        </div>

        {/* Allocation */}
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1.5">
            Allocation (USD)
          </label>
          <input
            type="number"
            value={allocationUsd}
            onChange={(e) => setAllocationUsd(Number(e.target.value))}
            min={10}
            max={100000}
            step={10}
            className="input-field"
            placeholder="Enter amount in USD"
          />
          <p className="text-xs text-text-secondary mt-1">
            Max margin per copied trade from this leader.
          </p>
        </div>

        {/* Max Leverage slider */}
        <div>
          <label className="block text-sm font-medium text-text-primary mb-1.5">
            Max Leverage: {maxLeverage}x
          </label>
          <input
            type="range"
            value={maxLeverage}
            onChange={(e) => setMaxLeverage(Number(e.target.value))}
            min={1}
            max={globalMax}
            step={1}
            className="w-full h-2 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent"
          />
          <div className="flex justify-between text-[10px] text-text-secondary mt-1">
            <span>1x</span>
            <span>{Math.round(globalMax / 2)}x</span>
            <span>{globalMax}x</span>
          </div>
          <div className="mt-2 p-2 bg-bg-secondary rounded text-[10px] text-text-secondary">
            <div className="font-medium mb-1">Perpl market limits (your setting is capped per market):</div>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5">
              {Object.entries(MARKET_MAX_LEV).map(([id, m]) => {
                const live = MARKET_CONFIGS[Number(id)]?.maxLeverage;
                const lev = live || m.max;
                return (
                  <span key={id}>
                    {m.symbol}: <span className={maxLeverage > lev ? 'text-warning font-medium' : ''}>{lev}x</span>
                  </span>
                );
              })}
            </div>
            {Object.entries(MARKET_MAX_LEV).some(([id, m]) => maxLeverage > (MARKET_CONFIGS[Number(id)]?.maxLeverage || m.max)) && (
              <div className="text-warning mt-1">Your setting exceeds some market limits — will be auto-capped when copying.</div>
            )}
          </div>
        </div>

        {/* Perpl warning */}
        {user && !user.perpl_linked && (
          <div className="p-3 bg-warning/10 border border-warning/20 rounded-lg">
            <p className="text-xs text-warning">
              You need to link your Perpl account before copy trading. Go to
              Settings to link your account.
            </p>
          </div>
        )}

        {/* Risk warning */}
        <div className="p-3 bg-danger/5 border border-danger/10 rounded-lg">
          <p className="text-xs text-text-secondary">
            <span className="text-danger font-semibold">Risk Warning:</span>{' '}
            Copy trading involves substantial risk of loss. Past performance is
            not indicative of future results. You may lose more than your
            initial allocation. Trade responsibly.
          </p>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button onClick={onClose} className="btn-secondary flex-1">
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={isFollowing || authLoading || allocationUsd <= 0}
            className="btn-primary flex-1"
          >
            {isFollowing || authLoading ? 'Processing...' : 'Confirm Follow'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
