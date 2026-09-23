import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import type { CopySubscription } from '@/lib/copyApi';
import { MARKETS } from '@/config/constants';
import { utcDate } from '@/lib/time';
import { formatUSD, shortenAddress } from '@/lib/formatters';

interface Props {
  sub: CopySubscription;
  onPause: (id: number) => Promise<void>;
  onResume: (id: number) => Promise<void>;
  onStop: (id: number) => Promise<void>;
}

const STATUS_STYLE: Record<string, string> = {
  active: 'text-success bg-success/10 border-success/25',
  paused: 'text-warning bg-warning/10 border-warning/25',
  stopped: 'text-text-secondary bg-text-secondary/10 border-text-secondary/20',
};

export default function PaperCopySubscriptionCard({ sub, onPause, onResume, onStop }: Props) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const run = async (fn: (id: number) => Promise<void>) => {
    setBusy(true);
    try {
      await fn(sub.id);
    } finally {
      setBusy(false);
    }
  };

  const markets = sub.allowed_markets && sub.allowed_markets.length
    ? sub.allowed_markets.map((m) => MARKETS[m]?.symbol || m).join(', ')
    : 'All';

  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <button
            onClick={() => navigate(`/copy/trader/${sub.trader_wallet}`)}
            className="text-sm font-semibold text-text-primary hover:text-accent transition-colors font-mono truncate"
          >
            {shortenAddress(sub.trader_wallet)}
          </button>
          <div className="text-[11px] text-text-secondary/60">
            Paper · {sub.sizing_mode} sizing · created {sub.created_at ? utcDate(new Date(sub.created_at).getTime() / 1000) : '—'}
          </div>
        </div>
        <span className={clsx('text-[10px] font-medium px-2 py-1 rounded-full border uppercase tracking-wide', STATUS_STYLE[sub.status] || STATUS_STYLE.stopped)}>
          {sub.status}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase">Allocation</div>
          <div className="text-xs font-bold text-text-primary tabular-nums">{formatUSD(sub.allocation_usd)}</div>
        </div>
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase">Max Lev</div>
          <div className="text-xs font-bold text-text-primary tabular-nums">{sub.max_leverage}x</div>
        </div>
        <div className="rounded-lg bg-bg-secondary/50 py-2">
          <div className="text-[10px] text-text-secondary/60 uppercase">Markets</div>
          <div className="text-xs font-bold text-text-primary truncate px-1">{markets}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-secondary/70">
        {sub.max_margin_per_trade != null && <span>Margin/trade: {formatUSD(sub.max_margin_per_trade)}</span>}
        {sub.max_daily_loss != null && <span>Daily loss: {formatUSD(sub.max_daily_loss)}</span>}
        {sub.max_total_loss != null && <span>Total loss: {formatUSD(sub.max_total_loss)}</span>}
        {sub.slippage_bps != null && <span>Slippage: {sub.slippage_bps} bps</span>}
        <span>New only: {sub.copy_new_only ? 'yes' : 'no'}</span>
      </div>

      {/* Controls */}
      {sub.status !== 'stopped' && (
        <div className="flex items-center gap-2 pt-1">
          {sub.status === 'active' ? (
            <button
              onClick={() => run(onPause)}
              disabled={busy}
              className="flex-1 text-xs font-medium py-2 rounded-lg bg-bg-secondary text-warning hover:bg-warning/10 transition-colors disabled:opacity-50"
            >
              Pause
            </button>
          ) : (
            <button
              onClick={() => run(onResume)}
              disabled={busy}
              className="flex-1 text-xs font-medium py-2 rounded-lg bg-bg-secondary text-success hover:bg-success/10 transition-colors disabled:opacity-50"
            >
              Resume
            </button>
          )}
          <button
            onClick={() => run(onStop)}
            disabled={busy}
            className="flex-1 text-xs font-medium py-2 rounded-lg bg-bg-secondary text-danger hover:bg-danger/10 transition-colors disabled:opacity-50"
          >
            Stop
          </button>
        </div>
      )}
    </div>
  );
}
