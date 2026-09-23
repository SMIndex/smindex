import { memo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import type { TraderListItem } from '@/lib/copyApi';
import { traderSignature } from '@/lib/copyApi';
import Sparkline from '@/components/copy/Sparkline';
import { formatCompact, formatPercent, shortenAddress } from '@/lib/formatters';

interface Props {
  trader: TraderListItem;
  series?: number[];
  isWatched: boolean;
  onWatchToggle: (wallet: string) => Promise<void>;
  onStartCopy: (trader: TraderListItem) => void;
}

function TraderCard({ trader, series, isWatched, onWatchToggle, onStartCopy }: Props) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const name = trader.display_name || shortenAddress(trader.wallet_address);
  const pnlUp = (trader.pnl_total ?? 0) >= 0;
  const roiUp = (trader.roi ?? 0) >= 0;

  const toggleWatch = async () => {
    setBusy(true);
    try {
      await onWatchToggle(trader.wallet_address);
    } finally {
      setBusy(false);
    }
  };

  const count = trader.active_positions_count;
  const hasActive = !!trader.has_active_positions;

  return (
    <div className={clsx(
      'rounded-xl bg-bg-card border p-4 transition-colors flex flex-col gap-3',
      hasActive ? 'border-accent/40 ring-1 ring-accent/10' : 'border-text-secondary/10 hover:border-accent/30',
    )}>
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-9 h-9 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center text-accent text-xs font-bold shrink-0">
            #{trader.rank}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-semibold text-text-primary truncate">{name}</span>
              {(trader.exchange ?? 'perpl') === 'hl' && (
                <span className="rd-mono text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0"
                  style={{ background: 'rgba(80,220,170,0.14)', color: '#3ecfa4', border: '1px solid rgba(80,220,170,0.3)' }}>HL</span>
              )}
              {trader.is_verified && (
                <svg className="w-3.5 h-3.5 text-accent shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              )}
            </div>
            <span className="text-[11px] text-text-secondary/60 font-mono">{shortenAddress(trader.wallet_address)}</span>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">PnL</div>
          <div className={clsx('text-xs font-bold tabular-nums', pnlUp ? 'text-success' : 'text-danger')}>
            {formatCompact(trader.pnl_total ?? 0)}
          </div>
        </div>
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">ROI</div>
          <div className={clsx('text-xs font-bold tabular-nums', roiUp ? 'text-success' : 'text-danger')}>
            {formatPercent(trader.roi ?? 0)}
          </div>
        </div>
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase tracking-wide">Volume</div>
          <div className="text-xs font-bold tabular-nums text-text-primary">{formatCompact(trader.volume ?? 0)}</div>
        </div>
      </div>

      {/* Active trades — FIXED height (badge row + one chip row) so the card never
          changes height / shifts the grid as state goes pending -> count -> chips. */}
      <div className="h-[44px] flex flex-col gap-1 overflow-hidden">
        {/* Row 1: state badge (always present, fixed height) */}
        <div className="flex items-center h-[18px]">
          {count != null && count > 0 ? (
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-success/12 text-success border border-success/25 whitespace-nowrap">
              {count} active trade{count > 1 ? 's' : ''}
            </span>
          ) : count === 0 ? (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/60 whitespace-nowrap">No active trades</span>
          ) : trader.active_positions_pending ? (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/50 animate-pulse whitespace-nowrap">Checking trades…</span>
          ) : (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/50 whitespace-nowrap">Open trades unavailable</span>
          )}
        </div>
        {/* Row 2: market chips, clamped to one line (active only; space reserved otherwise) */}
        <div className="flex items-center gap-1 h-[18px] overflow-hidden flex-nowrap">
          {count != null && count > 0 && (
            <>
              {trader.active_markets.slice(0, 3).map((m, i) => (
                <span
                  key={`${m.market_id}-${i}`}
                  className={clsx('shrink-0 text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap', m.side === 'long' ? 'bg-success/10 text-success' : 'bg-danger/10 text-danger')}
                >
                  {m.symbol} {m.side === 'long' ? 'Long' : 'Short'}
                </span>
              ))}
              {trader.active_markets.length > 3 && (
                <span className="shrink-0 text-[10px] text-text-secondary/60">+{trader.active_markets.length - 3}</span>
              )}
            </>
          )}
        </div>
      </div>

      {/* Equity sparkline — REAL data only; <3 points renders a dash, never a fake line */}
      <div className="rounded-[10px] px-2.5 py-2 flex items-center justify-center" style={{ background: 'var(--surface-2)', height: 46 }}>
        {(series?.length ?? 0) >= 3 ? (
          <Sparkline points={series} width={260} height={34} />
        ) : (
          <span className="rd-mono text-[11px]" style={{ color: 'var(--faint)' }}>—</span>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => navigate(`/copy/trader/${trader.wallet_address}`)}
          className="flex-1 text-xs font-medium py-2 rounded-lg bg-bg-secondary text-text-primary hover:bg-bg-secondary/70 transition-colors"
        >
          View Profile
        </button>
        <button
          onClick={toggleWatch}
          disabled={busy}
          className={clsx(
            'text-xs font-medium py-2 px-3 rounded-lg transition-colors disabled:opacity-50',
            isWatched
              ? 'bg-accent/15 text-accent border border-accent/30'
              : 'bg-bg-secondary text-text-secondary hover:text-text-primary',
          )}
          title={isWatched ? 'Unwatch' : 'Watch'}
        >
          {isWatched ? 'Watching' : 'Watch'}
        </button>
      </div>
      <button
        onClick={() => onStartCopy(trader)}
        className="w-full text-xs font-semibold py-2 rounded-lg bg-accent text-white hover:bg-accent/90 transition-colors"
      >
        Set up Copy
      </button>
    </div>
  );
}

// Re-render ONLY when this trader's render-relevant data or its watched state changes.
// The inline onWatchToggle/onStartCopy props (new identity each parent render) are
// intentionally ignored — they take the wallet/trader as an argument, so stale closures
// aren't a concern. This keeps cards visually stable during background polls.
export default memo(
  TraderCard,
  (a, b) => a.isWatched === b.isWatched && a.series === b.series && traderSignature(a.trader) === traderSignature(b.trader),
);
