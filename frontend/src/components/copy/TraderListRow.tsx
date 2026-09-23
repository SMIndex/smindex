import { memo } from 'react';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import type { TraderListItem } from '@/lib/copyApi';
import { traderSignature } from '@/lib/copyApi';
import Sparkline from '@/components/copy/Sparkline';
import { formatCompact, formatPercent, shortenAddress } from '@/lib/formatters';
import { relativeAge } from '@/lib/time';

interface Props {
  trader: TraderListItem;
  series?: number[];
  isWatched: boolean;
  onWatchToggle: (wallet: string) => Promise<void>;
  onStartCopy: (trader: TraderListItem) => void;
  onProfile: (wallet: string) => void;
}

export const LIST_GRID = '46px minmax(140px,1.6fr) .85fr .8fr .85fr 84px 1.1fr 1.3fr';

function rankStyle(rank: number): React.CSSProperties {
  if (rank === 1) return { background: 'linear-gradient(135deg,var(--gold),#e0a93a)', color: '#1a1205' };
  if (rank === 2) return { background: 'linear-gradient(135deg,var(--silver),#a9b0bd)', color: '#1a1d24' };
  if (rank === 3) return { background: 'linear-gradient(135deg,var(--bronze),#c47a44)', color: '#241405' };
  return { background: 'var(--accent-soft)', color: 'var(--accent-2)' };
}

// Activity badges (max 2 per row, priority order below — clutter guard).
// Thresholds documented in ANALYTICS_REPORT: fire badge at >=5 active days of
// the last 7 (or a fully-active NEW wallet with <7d history, labeled "(new)");
// trades badge at >=3 trades/day over 7d; consistency badge only when
// profitable in ALL four leaderboard windows simultaneously (flags == 15).
export function activityBadges(t: TraderListItem): { text: string; title: string }[] {
  const out: { text: string; title: string }[] = [];
  const a = t.activity;
  const f = t.fill_stats;
  if (a?.active_days_7d != null) {
    const denom = Math.min(7, a.history_days ?? 7);
    const isNew = (a.history_days ?? 7) < 7;
    if (a.active_days_7d >= 5 || (isNew && denom > 0 && a.active_days_7d >= denom)) {
      out.push({
        text: `🔥 ${a.active_days_7d}/${denom}d${isNew ? ' (new)' : ''}`,
        title: `Active ${a.active_days_7d} of the last ${denom} observed days — derived from leaderboard snapshots (30-min on Perpl, hourly on HL)${isNew ? ' (wallet has under 7 days of history)' : ''}`,
      });
    }
  }
  if (f?.trades_7d != null && f.trades_7d / 7 >= 3) {
    out.push({
      text: `⏱ ~${Math.round(f.trades_7d / 7)}/day`,
      title: `${f.trades_7d} closed trades in the last 7 days — from venue fill history${f.sample_capped ? ' (sampled; fetch was cap-limited)' : ''}`,
    });
  }
  if (t.activity?.consistency_flags === 15) {
    out.push({
      text: '📈 4w✓',
      title: 'Profitable in day, week, month AND all-time windows simultaneously — derived from leaderboard snapshots (30-min on Perpl, hourly on HL)',
    });
  }
  return out.slice(0, 2);
}

function TraderListRow({ trader, series, isWatched, onWatchToggle, onStartCopy, onProfile }: Props) {
  const navigate = useNavigate();
  void navigate;
  const name = trader.display_name || shortenAddress(trader.wallet_address);
  const badges = activityBadges(trader);
  const pnlUp = (trader.pnl_total ?? 0) >= 0;
  const roiUp = (trader.roi ?? 0) >= 0;
  const count = trader.active_positions_count;

  return (
    <div
      className="rd-row group grid items-center gap-3 px-4 py-3 rounded-[14px] transition-all"
      style={{ gridTemplateColumns: LIST_GRID, background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {/* Rank */}
      <div className="rd-mono font-extrabold text-[13px] w-[34px] h-[34px] rounded-[9px] flex items-center justify-center" style={rankStyle(trader.rank)}>
        {trader.rank}
      </div>
      {/* Trader */}
      <button onClick={() => onProfile(trader.wallet_address)} className="text-left min-w-0">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="rd-mono font-bold text-[14px] truncate" style={{ color: 'var(--text)' }}>{name}</span>
          {(trader.exchange ?? 'perpl') === 'hl' && (
            <span className="rd-mono text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0"
              style={{ background: 'rgba(80,220,170,0.14)', color: '#3ecfa4', border: '1px solid rgba(80,220,170,0.3)' }}>HL</span>
          )}
          {badges.map((b) => (
            <span key={b.text} title={b.title}
              className="rd-mono text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid var(--border)' }}>
              {b.text}
            </span>
          ))}
        </div>
        <div className="rd-mono text-[10.5px] truncate" style={{ color: 'var(--faint)' }}>
          {shortenAddress(trader.wallet_address)} · {(trader.exchange ?? 'perpl') === 'hl' ? 'on Hyperliquid' : 'on Monad'}
          {' · '}
          <span title="last detected fill (UTC)">
            {trader.last_fill_at
              ? relativeAge(Math.floor(Date.parse(trader.last_fill_at.endsWith('Z') ? trader.last_fill_at : trader.last_fill_at + 'Z') / 1000))
              : '—'}
          </span>
        </div>
      </button>
      {/* PnL */}
      <div className="rd-mono font-bold text-[14px] tabular-nums" style={{ color: pnlUp ? 'var(--green)' : 'var(--red)' }}>{formatCompact(trader.pnl_total ?? 0)}</div>
      {/* ROI */}
      <div className="rd-mono font-bold text-[14px] tabular-nums" style={{ color: roiUp ? 'var(--green)' : 'var(--red)' }}>{formatPercent(trader.roi ?? 0)}</div>
      {/* Volume */}
      <div className="rd-mono font-bold text-[14px] tabular-nums" style={{ color: 'var(--text)' }}>{formatCompact(trader.volume ?? 0)}</div>
      {/* Open pill (fixed-height to avoid layout shift) */}
      <div className="flex justify-start">
        <div className="min-w-[62px] h-[34px] rounded-[8px] flex flex-col items-center justify-center" style={{ background: 'var(--surface-2)' }}>
          {count != null ? (
            <span className="rd-mono font-bold text-[13px] leading-none" style={{ color: count > 0 ? 'var(--accent-2)' : 'var(--faint)' }}>{count}</span>
          ) : (
            <span className={clsx('rd-mono font-bold text-[12px] leading-none', trader.active_positions_pending && 'animate-pulse')} style={{ color: 'var(--faint)' }}>—</span>
          )}
          <span className="text-[9px] mt-0.5 leading-none" style={{ color: 'var(--faint)' }}>OPEN</span>
        </div>
      </div>
      {/* Equity sparkline — REAL data only. Fewer than 3 accumulated snapshot
          points (e.g. fresh HL ingest) renders an honest dash, never a fake line. */}
      <div className="rounded-[8px] px-2 py-1 flex items-center justify-center" style={{ background: 'var(--surface-2)' }}>
        {(series?.length ?? 0) >= 3 ? (
          <Sparkline points={series} width={120} height={30} />
        ) : (
          <span className="rd-mono text-[11px]" style={{ color: 'var(--faint)' }}>—</span>
        )}
      </div>
      {/* Actions */}
      <div className="flex items-center justify-end gap-1.5">
        <button onClick={() => onProfile(trader.wallet_address)} className="rd-btn-ghost text-[12px] font-medium px-2.5 py-1.5">Profile</button>
        <button
          onClick={() => onWatchToggle(trader.wallet_address)}
          className={clsx('text-[12px] font-medium px-2.5 py-1.5 rounded-[8px] transition-colors', isWatched ? '' : 'rd-btn-ghost')}
          style={isWatched ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid var(--border)' } : undefined}
          title={isWatched ? 'Unwatch' : 'Watch'}
        >
          {isWatched ? '★' : 'Watch'}
        </button>
        <button onClick={() => onStartCopy(trader)} className="rd-btn-primary text-[12px] px-3 py-1.5">Copy</button>
      </div>
    </div>
  );
}

export default memo(
  TraderListRow,
  (a, b) => a.isWatched === b.isWatched && a.series === b.series && traderSignature(a.trader) === traderSignature(b.trader),
);
