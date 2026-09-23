import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Link } from 'react-router-dom';
import { useCopyTrading } from '@/hooks/useCopyTrading';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import {
  getFollowedLeadersPositions,
  type FollowedLeader,
  type FollowedLeaderPosition,
} from '@/lib/api';
import { formatUSD, formatPrice, shortenAddress } from '@/lib/formatters';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import Modal from '@/components/common/Modal';
import CopyModal from '@/components/copytrade/CopyModal';
import EditFollowModal from '@/components/copytrade/EditFollowModal';

interface CopyTarget {
  leaderWallet: string;
  position: FollowedLeaderPosition;
}

function PositionRow({
  position,
  onCopy,
}: {
  position: FollowedLeaderPosition;
  onCopy: () => void;
}) {
  const isLong = position.side === 'long';
  const pnl = position.pnl ?? 0;
  const deposit = position.deposit ?? 0;
  const pnlPct = deposit > 0 ? (pnl / deposit) * 100 : 0;
  const mcfg = MARKET_CONFIGS[position.market_id];
  const priceDec = mcfg?.priceDecimals ?? 2;

  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-3 p-3 rounded-lg bg-bg-primary/60 hover:bg-bg-primary transition-colors">
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <span
          className={clsx(
            'text-[10px] font-bold px-2 py-0.5 rounded shrink-0',
            isLong ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger',
          )}
        >
          {isLong ? 'LONG' : 'SHORT'}
        </span>
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-text-primary">{position.symbol}</span>
            <span className="text-[10px] text-text-secondary">{position.leverage}x</span>
          </div>
          <div className="text-[10px] text-text-secondary mt-0.5">
            entry ${formatPrice(position.entry_price, priceDec)} • mark ${formatPrice(position.mark_price, priceDec)}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between sm:justify-end gap-4 sm:gap-6">
        <div className="text-right">
          <div className="text-xs text-text-secondary">{formatUSD(position.notional)}</div>
          <div className={clsx('text-xs font-semibold', pnl >= 0 ? 'text-success' : 'text-danger')}>
            {pnl >= 0 ? '+' : ''}{formatUSD(pnl)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
          </div>
        </div>
        <button
          onClick={onCopy}
          className={clsx(
            'shrink-0 text-xs font-semibold px-3 py-1.5 rounded-md transition-colors',
            isLong
              ? 'bg-success/15 text-success hover:bg-success/25'
              : 'bg-danger/15 text-danger hover:bg-danger/25',
          )}
        >
          Copy →
        </button>
      </div>
    </div>
  );
}

function LeaderCard({
  leader,
  onCopy,
  onEdit,
  onUnfollow,
}: {
  leader: FollowedLeader;
  onCopy: (p: FollowedLeaderPosition) => void;
  onEdit: () => void;
  onUnfollow: () => void;
}) {
  const initial = (leader.leader_display_name || leader.leader_wallet || 'L')[0].toUpperCase();
  const hasPositions = leader.positions.length > 0;

  return (
    <div className={clsx('card', !leader.is_active && 'opacity-70')}>
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
            <span className="text-sm font-bold text-accent">{initial}</span>
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-text-primary truncate">
                {leader.leader_display_name || shortenAddress(leader.leader_wallet)}
              </span>
              {!leader.is_active && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-bg-secondary text-text-secondary">PAUSED</span>
              )}
              {leader.auto_copy && (
                <span className="text-[10px] px-2 py-0.5 rounded bg-accent/10 text-accent">AUTO</span>
              )}
            </div>
            <div className="text-[11px] text-text-secondary font-mono mt-0.5">
              {shortenAddress(leader.leader_wallet)}
              <span className="mx-2 text-text-secondary/40">•</span>
              <span className="text-text-secondary">
                Alloc {formatUSD(leader.allocation_usd)} • Max {leader.max_leverage}x
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button onClick={onEdit} className="btn-secondary text-xs py-1 px-3">
            Edit
          </button>
          <button onClick={onUnfollow} className="btn-danger text-xs py-1 px-3">
            Unfollow
          </button>
        </div>
      </div>

      <div className="mt-3">
        {leader.error ? (
          <div className="text-xs text-warning bg-warning/5 rounded-md px-3 py-2">
            Could not read on-chain state: {leader.error}
          </div>
        ) : !hasPositions ? (
          <div className="text-xs text-text-secondary text-center py-4">
            No open positions right now
          </div>
        ) : (
          <div className="space-y-2">
            {leader.positions.map((p) => (
              <PositionRow key={p.market_id} position={p} onCopy={() => onCopy(p)} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function FollowingFeed() {
  const { isConnected } = useAuth();
  const { unfollow, isUnfollowing } = useCopyTrading();
  const toast = useToast();
  const [showPaused, setShowPaused] = useState(false);
  const [copyTarget, setCopyTarget] = useState<CopyTarget | null>(null);
  const [editFollow, setEditFollow] = useState<FollowedLeader | null>(null);
  const [confirmUnfollow, setConfirmUnfollow] = useState<string | null>(null);

  const { data: leaders, isLoading } = useQuery({
    queryKey: ['followed-leader-positions', showPaused],
    queryFn: () => getFollowedLeadersPositions(showPaused),
    enabled: isConnected,
    refetchInterval: 15000,
  });

  const summary = useMemo(() => {
    if (!leaders) return null;
    const active = leaders.filter((l) => l.is_active).length;
    const withPositions = leaders.filter((l) => l.positions.length > 0).length;
    const totalPositions = leaders.reduce((s, l) => s + l.positions.length, 0);
    return { active, total: leaders.length, withPositions, totalPositions };
  }, [leaders]);

  const handleUnfollow = async (leaderWallet: string) => {
    try {
      await unfollow(leaderWallet);
      toast.success('Unfollowed');
      setConfirmUnfollow(null);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Failed to unfollow');
    }
  };

  if (!isConnected) {
    return (
      <div className="card text-center py-12">
        <p className="text-sm text-text-secondary">Connect your wallet to see traders you follow.</p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!leaders || leaders.length === 0) {
    return (
      <div className="card text-center py-12">
        <h3 className="text-lg font-semibold text-text-primary mb-2">No Follows Yet</h3>
        <p className="text-sm text-text-secondary mb-4">
          Browse the Leaderboard to find traders to follow.
        </p>
        <Link to="/copy" className="text-sm text-accent hover:text-accent/80">
          Open Leaderboard
        </Link>
      </div>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          {summary && (
            <div className="text-xs text-text-secondary">
              <span className="text-text-primary font-semibold">{summary.withPositions}</span> of{' '}
              <span className="text-text-primary font-semibold">{summary.active}</span> followed traders have open positions
              {summary.totalPositions > 0 && (
                <> • <span className="text-text-primary font-semibold">{summary.totalPositions}</span> total positions</>
              )}
            </div>
          )}
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showPaused}
              onChange={(e) => setShowPaused(e.target.checked)}
              className="accent-accent"
            />
            Show paused
          </label>
        </div>

        <div className="space-y-3">
          {leaders.map((leader) => (
            <LeaderCard
              key={leader.leader_wallet}
              leader={leader}
              onCopy={(p) => setCopyTarget({ leaderWallet: leader.leader_wallet, position: p })}
              onEdit={() => setEditFollow(leader)}
              onUnfollow={() => setConfirmUnfollow(leader.leader_wallet)}
            />
          ))}
        </div>
      </div>

      {copyTarget && (
        <CopyModal
          position={{
            market_id: copyTarget.position.market_id,
            symbol: copyTarget.position.symbol,
            side: copyTarget.position.side,
            entry_price: copyTarget.position.entry_price,
            mark_price: copyTarget.position.mark_price,
            leverage: copyTarget.position.leverage,
            size: copyTarget.position.size,
          }}
          copiedFrom={copyTarget.leaderWallet}
          onClose={() => setCopyTarget(null)}
        />
      )}

      {editFollow && (
        <EditFollowModal
          follow={{
            leader_wallet: editFollow.leader_wallet,
            allocation_usd: editFollow.allocation_usd,
            max_leverage: editFollow.max_leverage,
            is_active: editFollow.is_active,
          }}
          onClose={() => setEditFollow(null)}
        />
      )}

      {confirmUnfollow && (
        <Modal
          isOpen={true}
          onClose={() => setConfirmUnfollow(null)}
          title="Confirm Unfollow"
        >
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              Stop following {shortenAddress(confirmUnfollow)}? Any open copied positions
              stay active — you'll just stop seeing this trader here.
            </p>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setConfirmUnfollow(null)}
                className="btn-secondary flex-1"
              >
                Cancel
              </button>
              <button
                onClick={() => handleUnfollow(confirmUnfollow)}
                disabled={isUnfollowing}
                className="btn-danger flex-1"
              >
                {isUnfollowing ? 'Unfollowing...' : 'Confirm Unfollow'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
