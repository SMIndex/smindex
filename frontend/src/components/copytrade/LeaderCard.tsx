import { clsx } from 'clsx';
import { shortenAddress, formatUSD, formatPercent, formatCompact } from '@/lib/formatters';
import type { Leader } from '@/types/copytrade';

interface LeaderCardProps {
  leader: Leader;
  onFollow: () => void;
  isFollowed?: boolean;
  onUnfollow?: () => void;
}

export default function LeaderCard({ leader, onFollow, isFollowed, onUnfollow }: LeaderCardProps) {
  return (
    <div className="card-glow group">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent/20 to-accent-dark/20 flex items-center justify-center border border-accent/10 group-hover:border-accent/30 transition-colors">
            <span className="text-xs font-bold text-accent">{leader.wallet_address[2]?.toUpperCase()}</span>
          </div>
          <div>
            <div className="text-xs text-text-secondary">#{leader.rank}</div>
            <div className="text-sm font-semibold text-text-primary font-mono">
              {shortenAddress(leader.wallet_address)}
            </div>
          </div>
        </div>
        {isFollowed ? (
          <button
            onClick={(e) => { e.stopPropagation(); onUnfollow?.(); }}
            className="px-3 py-1.5 rounded-lg text-xs font-bold bg-danger/10 text-danger border border-danger/20 hover:bg-danger hover:text-white active:scale-95 transition-all duration-200"
          >
            Unfollow
          </button>
        ) : (
          <button
            onClick={(e) => { e.stopPropagation(); onFollow(); }}
            className="px-3 py-1.5 rounded-lg text-xs font-bold bg-accent/10 text-accent border border-accent/20 hover:bg-accent hover:text-white hover:shadow-lg hover:shadow-accent/25 active:scale-95 transition-all duration-200"
          >
            Follow
          </button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-[10px] text-text-secondary mb-0.5">PnL</div>
          <div className={clsx('text-xs font-bold', leader.pnl_total >= 0 ? 'text-success' : 'text-danger')}>
            {leader.pnl_total >= 0 ? '+' : ''}{formatUSD(leader.pnl_total)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-text-secondary mb-0.5">ROI</div>
          <div className={clsx('text-xs font-bold', leader.roi >= 0 ? 'text-success' : 'text-danger')}>
            {formatPercent(leader.roi)}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-text-secondary mb-0.5">Volume</div>
          <div className="text-xs font-bold text-text-primary">{formatCompact(leader.volume)}</div>
        </div>
      </div>

      {(leader.followers_count ?? 0) > 0 && (
        <div className="mt-2 pt-2 border-t border-text-secondary/5 flex items-center gap-1 text-[10px] text-text-secondary">
          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          {leader.followers_count} followers
        </div>
      )}
    </div>
  );
}
