import React, { useEffect, useState } from 'react';
import { getHlBasis, getAdminMe, adminHideTrader, adminUnhideTrader, getTraderHlHistory, type HlBasisRow, type HlHistoryResponse, type HlPositionHistory, type HlHistoryEntry } from '@/lib/copyApi';
import { clsx } from 'clsx';
import { useTrader } from '@/hooks/useCopyV1';
import Sparkline from '@/components/copy/Sparkline';
import StartPaperCopyModal from '@/components/copy/StartPaperCopyModal';
import LiveCopyModal, { type LiveCopyPosition } from '@/components/copy/LiveCopyModal';
import OpenDuration, { formatOpenedDate, openedDateLabel, OpenDurationOrBound } from '@/components/copy/OpenDuration';
import { MARKETS } from '@/config/constants';
import type { CreateSubscriptionPayload, CopySubscription, EquityTimeframe, HlProfileState, HlPosition, HlTpslOrder } from '@/lib/copyApi';
import { formatCompact, formatPercent, formatPrice, formatSize, shortenAddress } from '@/lib/formatters';
import { humanPx } from '@/lib/analyticsCopy';
import { relativeAge, compactDuration, utcDateTime } from '@/lib/time';
import type { TraderActivity, TraderFillStats } from '@/lib/copyApi';

interface Props {
  wallet: string;
  timeframe?: EquityTimeframe;
  exchange?: string;   // 'perpl' (default) | 'hl'
  isWatched: boolean;
  onWatchToggle: (wallet: string) => Promise<void>;
  onCreateSub: (payload: CreateSubscriptionPayload) => Promise<CopySubscription>;
  onClose: () => void;
}

const TF_LABEL: Record<EquityTimeframe, string> = { '24h': 'last 24h', '7d': 'last 7d', '30d': 'last 30d', all: 'all time' };

function Stat({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'up' | 'down' | 'neutral' }) {
  return (
    <div className="px-3 py-3">
      <div className="text-[9.5px] font-bold uppercase tracking-[1px] rd-sans" style={{ color: 'var(--faint)' }}>{label}</div>
      <div className="rd-mono font-bold text-[19px] mt-1 tabular-nums" style={{ color: tone === 'up' ? 'var(--green)' : tone === 'down' ? 'var(--red)' : 'var(--text)' }}>{value}</div>
    </div>
  );
}

export default function TraderProfileModal({ wallet, timeframe = '7d', exchange = 'perpl', isWatched, onWatchToggle, onCreateSub, onClose }: Props) {
  const { detail, positions, positionsError, perplOrders, equity, equityTimes, hlState, hlError, loading, equityPending, hlPending } = useTrader(wallet, timeframe, exchange);
  const isHl = exchange === 'hl';
  const [showCopy, setShowCopy] = useState(false);
  const [liveTarget, setLiveTarget] = useState<LiveCopyPosition | null>(null);
  // Admin moderation affordance (cosmetic — routes are server-enforced)
  const [isAdmin, setIsAdmin] = useState(false);
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    getAdminMe().then((r) => setIsAdmin(!!r.is_admin)).catch(() => setIsAdmin(false));
  }, []);
  useEffect(() => { setHidden(!!(detail?.profile as any)?.is_hidden); }, [detail]);
  const toggleHidden = async () => {
    try {
      if (hidden) await adminUnhideTrader(exchange, wallet);
      else await adminHideTrader(exchange, wallet);
      setHidden(!hidden);
    } catch { /* 403 for non-admins; nothing to do */ }
  };
  const head = detail?.stats;
  const name = detail?.profile?.display_name || shortenAddress(wallet);

  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !showCopy && !liveTarget) onClose(); };
    document.addEventListener('keydown', onEsc);
    document.body.style.overflow = 'hidden';
    return () => { document.removeEventListener('keydown', onEsc); document.body.style.overflow = ''; };
  }, [onClose, showCopy, liveTarget]);

  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center p-3 md:p-8 pb-[calc(0.75rem+env(safe-area-inset-bottom))] md:pb-8 overflow-y-auto rd-sans"
      style={{ background: 'rgba(5,5,10,.62)', backdropFilter: 'blur(7px)' }}
      onClick={onClose}
    >
      <div className={clsx('rd-modal-in w-full rounded-[22px] my-4', isHl ? 'max-w-[min(1200px,94vw)]' : 'max-w-[960px]')} style={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', boxShadow: 'var(--shadow)' }} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between gap-4 p-5 border-b" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center gap-3 min-w-0">
            <div className="rd-mono font-extrabold text-[13px] w-[38px] h-[38px] rounded-[10px] flex items-center justify-center shrink-0" style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
              {head?.rank ? `#${head.rank}` : '—'}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="rd-mono font-bold text-[22px]" style={{ color: 'var(--text)' }}>{name}</span>
                {isHl && (
                  <span className="rd-mono text-[10px] font-bold px-2 py-0.5 rounded"
                    style={{ background: 'rgba(80,220,170,0.14)', color: '#3ecfa4', border: '1px solid rgba(80,220,170,0.3)' }}>Hyperliquid</span>
                )}
              </div>
              <div className="rd-mono text-[12px] break-all" style={{ color: 'var(--faint)' }}>{wallet}</div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button onClick={() => onWatchToggle(wallet)} className={clsx('text-[12px] font-medium px-3 py-2 rounded-[9px]', isWatched ? '' : 'rd-btn-ghost')} style={isWatched ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid var(--border)' } : undefined}>{isWatched ? 'Watching' : 'Watch'}</button>
            {isAdmin && (
              <button onClick={toggleHidden} className="rd-btn-ghost text-[12px] px-3 py-2"
                style={{ color: hidden ? 'var(--green)' : 'var(--red)' }}>
                {hidden ? 'Unhide' : 'Hide'}
              </button>
            )}
            <button onClick={() => setShowCopy(true)} className="rd-btn-primary text-[12px] px-3 py-2">Set up copy</button>
            <button onClick={onClose} className="rd-btn-ghost w-9 h-9 flex items-center justify-center text-lg" aria-label="Close">×</button>
          </div>
        </div>

        <div className="p-5 space-y-4">
          {/* Stat grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 rounded-[14px] overflow-hidden" style={{ background: 'var(--surface-2)', gap: '1px' }}>
            <div style={{ background: 'var(--surface)' }}><Stat label="PnL" value={head?.pnl_total != null ? formatCompact(head.pnl_total) : '—'} tone={head?.pnl_total != null ? (head.pnl_total >= 0 ? 'up' : 'down') : 'neutral'} /></div>
            <div style={{ background: 'var(--surface)' }}><Stat label="ROI" value={head?.roi != null ? formatPercent(head.roi) : '—'} tone={head?.roi != null ? (head.roi >= 0 ? 'up' : 'down') : 'neutral'} /></div>
            <div style={{ background: 'var(--surface)' }}><Stat label="Volume" value={head?.volume != null ? formatCompact(head.volume) : '—'} /></div>
            <div style={{ background: 'var(--surface)' }}><Stat label="On Leaderboard" value={head?.on_leaderboard ? 'Yes' : 'No'} /></div>
          </div>

          {/* Activity analytics (Tier-1 snapshots + Tier-2 fills) */}
          <ActivitySection activity={detail?.activity ?? null} fillStats={detail?.fill_stats ?? null} isHl={isHl} />

          {/* Equity curve */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <div className="text-[9.5px] font-bold uppercase tracking-[1px]" style={{ color: 'var(--faint)' }}>Equity curve</div>
              <div className="text-[11px]" style={{ color: 'var(--faint)' }}>Cumulative PnL · {TF_LABEL[timeframe]} · {positions.length} open</div>
            </div>
            <div className="rounded-[12px] px-3 py-2" style={{ background: 'var(--surface-2)' }}>
              {equity.length >= (isHl ? 3 : 2) ? <Sparkline points={equity} times={isHl ? equityTimes : undefined} width={880} height={140} />
                : <div className="h-[140px] flex items-center justify-center text-[11px]" style={{ color: 'var(--faint)' }}>{loading || equityPending ? 'Loading…' : isHl ? 'No PnL history on Hyperliquid for this account.' : 'No equity history yet.'}</div>}
            </div>
          </div>

          {/* Positions */}
          {isHl ? (
            <HlSection state={hlState} error={hlError} loading={loading || hlPending}
              onCopy={(p) => setLiveTarget({ market_id: p.perpl_market_id!, symbol: p.coin, side: p.side, entry_price: p.entry_px, mark_price: p.mark_px, leverage: p.leverage, size: p.size, unrealized_pnl: p.unrealized_pnl, venue: 'hl', opened_at: p.opened_at, opened_before: p.opened_before, tp_px: p.tp_px, sl_px: p.sl_px, has_tpsl: p.has_tpsl })} />
          ) : (
          <div>
            {/* Label fix (Wallet Explorer Part 3): resting TP/SL ARE decoded
                on-chain (getPerpOrderLocks) — the old "not publicly visible"
                claim was false. Rendered per position below. */}
            <div className="text-[9.5px] font-bold uppercase tracking-[1px] mb-2" style={{ color: 'var(--faint)' }}>Live on-chain positions <span className="font-normal normal-case">(read-only · resting TP/SL decoded from on-chain order locks)</span></div>
            {loading ? (
              <div className="text-center text-[11px] py-6" style={{ color: 'var(--faint)' }}>Checking active trades…</div>
            ) : positions.length === 0 ? (
              <div className="text-center text-[11px] py-6" style={{ color: 'var(--faint)' }}>{positionsError ? 'Open trades unavailable.' : 'No open positions right now.'}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px] rd-mono">
                  <thead>
                    <tr className="text-left" style={{ color: 'var(--faint)' }}>
                      {['Market', 'Side', 'Size', 'Entry', 'Mark', 'Lev', 'TP/SL', 'Opened', 'Active', 'PnL', 'Copy'].map((h) => (
                        <th key={h} className="py-2 px-2 text-[10px] uppercase font-semibold rd-sans tracking-wide">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p, i) => {
                      const dec = MARKETS[p.market_id]?.decimals ?? 2;
                      // resting on-chain triggers for this market (order locks)
                      const trig = perplOrders.filter((o) => o.is_trigger && o.market_id === p.market_id);
                      const sl = trig.find((o) => o.order_type === 'stop_loss');
                      const tp = trig.find((o) => o.order_type === 'take_profit');
                      return (
                        <tr key={`${p.market_id}-${i}`} style={{ borderTop: '1px solid var(--border)' }}>
                          <td className="py-2.5 px-2 font-bold" style={{ color: 'var(--text)' }}>{p.symbol}</td>
                          <td className="py-2.5 px-2 font-semibold" style={{ color: p.side === 'long' ? 'var(--green)' : 'var(--red)' }}>{p.side.toUpperCase()}</td>
                          <td className="py-2.5 px-2 tabular-nums">{formatSize(p.size)}</td>
                          <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{formatPrice(p.entry_price, dec)}</td>
                          <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{formatPrice(p.mark_price, dec)}</td>
                          <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{p.leverage}x</td>
                          <td className="py-2.5 px-2 tabular-nums text-[11px] whitespace-nowrap" style={{ color: 'var(--dim)' }}
                            title="Resting trigger orders decoded from on-chain order locks (getPerpOrderLocks)">
                            {sl || tp
                              ? [sl?.price != null ? `SL ${formatPrice(sl.price, dec)}` : null,
                                 tp?.price != null ? `TP ${formatPrice(tp.price, dec)}` : null].filter(Boolean).join(' · ')
                              : 'none resting'}
                          </td>
                          <td className="py-2.5 px-2 tabular-nums whitespace-nowrap" style={{ color: 'var(--dim)' }}>{formatOpenedDate(p.opened_at)}</td>
                          <td className="py-2.5 px-2" style={{ color: 'var(--dim)' }}><OpenDuration openedAt={p.opened_at} /></td>
                          <td className="py-2.5 px-2 tabular-nums font-semibold" style={{ color: p.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{formatCompact(p.pnl)}</td>
                          <td className="py-2.5 px-2">
                            <button onClick={() => setLiveTarget({ market_id: p.market_id, symbol: p.symbol, side: p.side, entry_price: p.entry_price, mark_price: p.mark_price, leverage: p.leverage, size: p.size, venue: 'perpl', opened_at: p.opened_at })} className="rd-btn-primary text-[11px] px-2.5 py-1">Copy</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          )}

          <div className="text-center">
            <button onClick={onClose} className="text-[12px]" style={{ color: 'var(--faint)' }}>← Back to discover</button>
          </div>
        </div>
      </div>

      {showCopy && (
        <StartPaperCopyModal isOpen={showCopy} onClose={() => setShowCopy(false)} traderWallet={wallet} traderName={detail?.profile?.display_name} exchange={exchange} onSubmit={onCreateSub} />
      )}
      {liveTarget && (
        <LiveCopyModal isOpen={!!liveTarget} onClose={() => setLiveTarget(null)} traderWallet={wallet} traderName={detail?.profile?.display_name} position={liveTarget} />
      )}
    </div>
  );
}


// ---------- Activity analytics block (Tier 1 + Tier 2) ----------

// Label fix (Part 3): Perpl snapshots are written every 30 min (ws_manager
// 1800s loop); only the HL leaderboard ingest is hourly.
const snapSrc = (isHl: boolean) =>
  `derived from ${isHl ? 'hourly' : '30-min'} leaderboard snapshots`;
const FILL_SRC = 'last 7 days, from venue fill history';

const FLAG_LABELS: [number, string][] = [[1, 'day'], [2, 'week'], [4, 'month'], [8, 'all-time']];

function ActivitySection({ activity, fillStats, isHl }: {
  activity: TraderActivity | null; fillStats: TraderFillStats | null; isHl: boolean;
}) {
  if (!activity && !fillStats) return null;
  const a = activity;
  const f = fillStats;
  const denom = a?.history_days != null ? Math.min(7, a.history_days) : 7;
  const isNew = (a?.history_days ?? 7) < 7;
  const lastActiveSec = a?.last_active_at
    ? Math.floor(Date.parse(a.last_active_at.endsWith('Z') ? a.last_active_at : a.last_active_at + 'Z') / 1000) : null;
  const profitableWindows = a?.consistency_flags != null
    ? FLAG_LABELS.filter(([b]) => (a.consistency_flags! & b) !== 0).map(([, l]) => l) : null;

  const Cell = ({ label, value, title, tone }: { label: string; value: string; title: string; tone?: 'up' | 'down' }) => (
    <div title={title}>
      <span className="block text-[9px] uppercase tracking-wide" style={{ color: 'var(--faint)' }}>{label}</span>
      <span className="rd-mono font-bold text-[12.5px] tabular-nums"
        style={{ color: tone === 'up' ? 'var(--green)' : tone === 'down' ? 'var(--red)' : 'var(--text)' }}>{value}</span>
    </div>
  );

  return (
    <div className="rounded-[12px] px-3 py-2.5" style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="text-[9.5px] font-bold uppercase tracking-[1px]" style={{ color: 'var(--faint)' }}>Activity</div>
        {f?.sample_capped && (
          <span className="text-[9px]" style={{ color: 'var(--faint)' }} title="This wallet trades more than one sampling run can fetch — fill stats are a labeled sample, not exhaustive">
            sampled
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-5 gap-x-4 gap-y-2">
        <Cell label="Active days"
          value={a?.active_days_7d != null ? `${a.active_days_7d}/${denom}d${isNew ? ' (new)' : ''}` : '—'}
          title={`Days with traded volume in the last ${denom} observed days — ${snapSrc(isHl)}${isNew ? '. Wallet has under 7 days of snapshot history' : ''}`} />
        <Cell label="Last active"
          value={lastActiveSec ? `${relativeAge(lastActiveSec)} ago` : '—'}
          title={lastActiveSec ? `${utcDateTime(lastActiveSec)} — ${snapSrc(isHl)}` : snapSrc(isHl)} />
        <Cell label="Vol 24h"
          value={a?.vol_velocity_24h != null ? formatCompact(a.vol_velocity_24h) : '—'}
          title={`Traded volume over the last ~24h — ${snapSrc(isHl)}`} />
        <Cell label="Consistency"
          value={profitableWindows == null ? '—' : profitableWindows.length ? `${profitableWindows.length}/4 windows` : '0/4 windows'}
          title={profitableWindows == null
            ? `Not derivable on this venue's snapshots`
            : `Profitable right now in: ${profitableWindows.length ? profitableWindows.join(', ') : 'no window'} — absence from a window means "not provably profitable there", not losing. ${snapSrc(isHl)}`} />
        <Cell label="PnL trend 7d"
          value={a?.pnl_trend_7d != null ? `${a.pnl_trend_7d >= 0 ? '+' : ''}${formatCompact(a.pnl_trend_7d)}/d` : '—'}
          tone={a?.pnl_trend_7d != null ? (a.pnl_trend_7d >= 0 ? 'up' : 'down') : undefined}
          title={`Least-squares slope of the week-window PnL over the last 7 daily snapshots — ${snapSrc(isHl)}`} />
        {isHl && (
          <>
            <Cell label="Trades/day"
              value={f?.trades_7d != null ? `~${(f.trades_7d / 7).toFixed(1)}` : '—'}
              title={f?.trades_7d != null ? `${f.trades_7d} closed trades — ${FILL_SRC}` : FILL_SRC} />
            <Cell label="Win rate"
              value={f?.win_rate_7d != null
                ? `${(f.win_rate_7d * 100).toFixed(0)}% (${f.trades_7d} trades)${f.profit_factor != null ? ` · PF ${f.profit_factor >= 999 ? '∞' : f.profit_factor.toFixed(1)}` : ''}`
                : f?.trades_7d != null ? `— (${f.trades_7d} trades — sample too small)` : '—'}
              title={`Share of closed trades with positive PnL; hidden under 10 trades. PF = gross wins ÷ gross losses over the same closes (∞ = no losses in window) — a high win rate with PF < 1 gives it all back — ${FILL_SRC}`} />
            <Cell label="Max DD 7d"
              value={f?.max_drawdown_pct_7d != null
                ? `${(f.max_drawdown_pct_7d * 100).toFixed(1)}%`
                : f?.max_drawdown_7d != null && f.max_drawdown_7d > 0 ? formatCompact(f.max_drawdown_7d) : '—'}
              title={`Peak-to-trough of the cumulative realized-PnL path over the 7d closes (% of account value when the sweep knows it, else USD) — ${FILL_SRC}`} />
            <Cell label="Avg hold"
              value={f?.avg_hold_minutes != null ? compactDuration(f.avg_hold_minutes * 60) : '—'}
              title={`Open→close matched within the window; needs ≥5 matched pairs — ${FILL_SRC}`} />
            <Cell label="Avg size"
              value={f?.avg_trade_notional != null ? formatCompact(f.avg_trade_notional) : '—'}
              title={`Mean notional of closed trades — ${FILL_SRC}`} />
            <Cell label="Maker"
              value={f?.maker_ratio != null ? `${(f.maker_ratio * 100).toFixed(0)}%` : '—'}
              title={`Share of fills that did not cross the spread — ${FILL_SRC}`} />
          </>
        )}
      </div>
    </div>
  );
}


// ---------- Hyperliquid profile depth (phase 4) ----------

function fmtPx(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  // explicit locale: system locale grouping (e.g. 1,34,151.2 lakh) must never leak in
  return v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 1 }) : String(Number(v.toPrecision(5)));
}

function HlOrderRow({ o }: { o: HlTpslOrder }) {
  const px = o.trigger_px ?? o.limit_px;
  return (
    <div className="flex items-center gap-2 text-[11.5px] rd-mono py-0.5 whitespace-nowrap" style={{ color: 'var(--dim)' }}>
      <span className="font-semibold" style={{ color: o.side === 'buy' ? 'var(--green)' : 'var(--red)' }}>{o.side.toUpperCase()}</span>
      <span>{o.coin}</span>
      <span className="tabular-nums">@ {fmtPx(px)}</span>
      {/* HL semantics: a position-TP/SL with size 0 applies to the ENTIRE position */}
      <span className="tabular-nums">sz {o.size === 0 && o.tpsl ? 'all' : formatSize(o.size)}</span>
      {o.is_trigger && <span className="text-[9px] px-1 rounded" style={{ background: 'var(--surface-2)', color: 'var(--faint)' }}>{o.tpsl ? 'TP/SL' : 'TRIGGER'}</span>}
    </div>
  );
}

// ---------- per-position build history (drawer) ----------

const HIST_PILL: Record<string, (side: string) => React.CSSProperties> = {
  OPEN: (side) => side === 'long'
    ? { background: 'rgba(20,184,166,0.15)', color: '#14b8a6' }
    : { background: 'rgba(236,72,153,0.15)', color: '#ec4899' },
  ADD: (side) => HIST_PILL.OPEN(side),
  REDUCE: () => ({ background: 'var(--surface-2)', color: 'var(--faint)' }),
  'PARTIAL CLOSE': () => ({ background: 'var(--surface-2)', color: 'var(--dim)' }),
  FLIP: () => ({ background: 'rgba(245,158,11,0.15)', color: '#f59e0b' }),
  CLOSE: () => ({ background: 'var(--surface-2)', color: 'var(--dim)' }),
};

function histTime(t: number): string {
  // UTC per design grammar: "08-12 14:03"
  return new Date(t * 1000).toISOString().slice(5, 16).replace('T', ' ');
}

export function HistoryDrawer({ h, ageSec }: { h: HlPositionHistory; ageSec: number | null }) {
  const dated = h.dated;
  const datedWhen = dated?.opened_at ?? dated?.opened_before ?? null;
  const datedSrc = dated?.source === 'funding' ? 'funding ledger'
    : dated?.source === 'bound' ? 'fills bound (earliest provably-open moment)' : dated?.source;
  return (
    <div className="rounded-[10px] px-3 py-2 my-1" style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-center gap-2 flex-wrap mb-1.5">
        <span className="text-[9.5px] font-bold uppercase tracking-[1px] rd-sans" style={{ color: 'var(--faint)' }}>
          Position build history · {h.coin} {h.side}
        </span>
        <span className="text-[9.5px] rd-sans" style={{ color: 'var(--faint)' }}
          title="Derived from the wallet's venue fills (same fetch the open-date resolution uses); refreshes with the profile cache.">
          fills as of {ageSec != null ? `${Math.max(1, Math.round(ageSec / 60))}m` : '—'} · cached
        </span>
        {h.mismatch && (
          <span className="text-[9.5px] rd-sans" style={{ color: 'var(--gold, #f59e0b)' }}
            title={`Derived running average ${h.derived_entry} does not reproduce the venue's entry ${h.venue_entry} within rounding — the venue number is authoritative.`}>
            derived avg {fmtPx(h.derived_entry)} ≠ venue {fmtPx(h.venue_entry)} — venue shown
          </span>
        )}
      </div>
      {h.truncated && (
        <div className="text-[10.5px] mb-1 rd-sans" style={{ color: 'var(--faint)' }}>
          earlier fills unavailable — position dated to{' '}
          {datedWhen != null ? formatOpenedDate(dated?.opened_at ?? null) || `>${formatOpenedDate(dated?.opened_before ?? null)}` : 'unknown'}
          {datedSrc ? ` via ${datedSrc}` : ''} · running avg starts from the first available fill (≈)
        </div>
      )}
      <table className="w-full text-[11px] rd-mono">
        <thead>
          <tr className="text-left" style={{ color: 'var(--faint)' }}>
            {['Time (UTC)', 'Action', 'Size', 'Price', 'Run size', 'Run avg entry'].map((hd) => (
              <th key={hd} className="py-1 pr-3 text-[9px] uppercase rd-sans font-semibold whitespace-nowrap">{hd}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {h.entries.map((e: HlHistoryEntry, i: number) => (
            <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
              <td className="py-1 pr-3 tabular-nums whitespace-nowrap" style={{ color: 'var(--dim)' }}>{histTime(e.t)}</td>
              <td className="py-1 pr-3">
                <span className="text-[9px] px-1.5 py-0.5 rounded rd-sans font-semibold whitespace-nowrap"
                  style={(HIST_PILL[e.action] || HIST_PILL.REDUCE)(h.side)}>
                  {e.action}{e.fills > 1 ? ` · ${e.fills}f` : ''}
                </span>
              </td>
              <td className="py-1 pr-3 tabular-nums">{formatSize(e.size)}</td>
              <td className="py-1 pr-3 tabular-nums" style={{ color: 'var(--dim)' }}>{fmtPx(e.px)}</td>
              <td className="py-1 pr-3 tabular-nums">{formatSize(e.run_size)}</td>
              <td className="py-1 pr-3 tabular-nums" style={{ color: 'var(--text)' }}>
                {e.run_avg != null ? `${e.approx ? '≈' : ''}${fmtPx(e.run_avg)}` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {h.elided > 0 && (
        <div className="text-[10px] mt-1 rd-sans" style={{ color: 'var(--faint)' }}>
          … {h.elided} middle entries elided (history capped for display)
        </div>
      )}
    </div>
  );
}

function HlSection({ state, error, loading, onCopy }: {
  state: HlProfileState | null; error: boolean; loading: boolean;
  onCopy: (p: HlPosition) => void;
}) {
  // Cross-venue basis (phase 5): HL mid vs Perpl mark for mapped markets.
  const [basis, setBasis] = useState<HlBasisRow[]>([]);
  // Per-position build history — no venue calls (served from the dating
  // worker's cache); brief retries while the worker is still deriving.
  const [hist, setHist] = useState<HlHistoryResponse | null>(null);
  const [openHist, setOpenHist] = useState<string | null>(null);
  useEffect(() => {
    const w = state?.wallet;
    if (!w) return;
    let stop = false;
    let tries = 0;
    const pull = () => getTraderHlHistory(w).then((h) => {
      if (stop) return;
      setHist(h);
      if ((h.pending || h.histories.length === 0) && tries++ < 5) setTimeout(pull, 4000);
    }).catch(() => {});
    pull();
    return () => { stop = true; };
  }, [state?.wallet]);
  const histFor = (p: HlPosition): HlPositionHistory | null =>
    hist?.histories.find((h) => h.coin === p.coin && h.side === p.side) ?? null;
  useEffect(() => {
    let cancelled = false;
    getHlBasis().then((b) => { if (!cancelled) setBasis(b); }).catch(() => {});
    const id = setInterval(() => { getHlBasis().then((b) => { if (!cancelled) setBasis(b); }).catch(() => {}); }, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  if (loading && !state) return <div className="text-center text-[11px] py-6" style={{ color: 'var(--faint)' }}>Loading Hyperliquid state…</div>;
  if (error || !state) return <div className="text-center text-[11px] py-6" style={{ color: 'var(--faint)' }}>Hyperliquid state unavailable right now.</div>;
  const { positions, adds, pending_entries, copyable_pct, account_value } = state;
  const MAX_ROWS = 8;
  const posCoins = new Set(positions.filter((p) => p.perpl_market_id != null).map((p) => p.coin));
  const basisRows = basis.filter((b) => posCoins.has(b.coin) && b.basis_bps != null);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-[9.5px] font-bold uppercase tracking-[1px]" style={{ color: 'var(--faint)' }}>
          Live Hyperliquid positions <span className="font-normal normal-case">(read-only · account {formatCompact(account_value)})</span>
        </div>
        {copyable_pct != null && (
          <span className="rd-mono text-[11px] font-semibold px-2 py-1 rounded-[7px]"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
            Copyable on Perpl: {copyable_pct}%
          </span>
        )}
      </div>

      {basisRows.length > 0 && (
        <div className="rounded-[10px] px-3 py-2 flex flex-wrap gap-x-4 gap-y-1" style={{ background: 'var(--surface-2)' }}>
          {basisRows.map((b) => (
            <span key={b.coin} className="rd-mono text-[11px]" style={{ color: 'var(--dim)' }}>
              {b.coin}: HL {fmtPx(b.hl_mid)} vs Perpl {fmtPx(b.perpl_mark)} →{' '}
              <b style={{ color: Math.abs(b.basis_bps!) > 20 ? 'var(--red)' : 'var(--green)' }}>
                {b.basis_bps! >= 0 ? '+' : ''}{b.basis_bps!.toFixed(1)} bps
              </b>
              {b.hl_funding_hourly != null && b.perpl_funding != null && (
                <span style={{ color: 'var(--faint)' }}> · fund HL {(b.hl_funding_hourly * 100).toFixed(4)}%/h vs P {(b.perpl_funding * 100).toFixed(2)}%</span>
              )}
            </span>
          ))}
        </div>
      )}

      {positions.length === 0 ? (
        <div className="text-center text-[11px] py-6" style={{ color: 'var(--faint)' }}>No open positions right now.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] rd-mono">
            <thead>
              <tr className="text-left" style={{ color: 'var(--faint)' }}>
                {['Market', 'Side', 'Size', 'Avg entry', 'Mark', 'Lev', 'Liq', 'uPnL', 'Opened', 'Active', 'TP/SL', 'R:R', ''].map((h) => (
                  <th key={h} className={clsx('py-2 px-1.5 text-[10px] uppercase font-semibold rd-sans tracking-wide whitespace-nowrap', h === 'TP/SL' && 'min-w-[120px]')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p, i) => {
                const unmapped = p.perpl_market_id == null;
                const h = histFor(p);
                const hk = p.coin + '|' + p.side;
                const expanded = openHist === hk;
                return (
                  <React.Fragment key={p.coin + '-' + i}>
                  <tr style={{ borderTop: '1px solid var(--border)', opacity: unmapped ? 0.45 : 1 }}>
                    <td className="py-2.5 px-2 font-bold" style={{ color: 'var(--text)' }}>
                      {p.coin}
                      {unmapped && <span className="ml-1.5 text-[9px] font-normal" style={{ color: 'var(--faint)' }}>Not on Perpl</span>}
                    </td>
                    <td className="py-2.5 px-2 font-semibold" style={{ color: p.side === 'long' ? 'var(--green)' : 'var(--red)' }}>{p.side.toUpperCase()}</td>
                    <td className="py-2.5 px-2 tabular-nums whitespace-nowrap">
                      {formatSize(p.size)}
                      {h && (
                        <button type="button" onClick={() => setOpenHist(expanded ? null : hk)}
                          title="Position build history — how this position was built, fill by fill"
                          className="ml-1 align-middle px-1 rounded hover:bg-[var(--surface-2)]"
                          style={{ color: expanded ? 'var(--accent-2)' : 'var(--faint)' }}>
                          <svg className={clsx('w-3 h-3 inline transition-transform', expanded && 'rotate-180')} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.2} d="M19 9l-7 7-7-7" />
                          </svg>
                        </button>
                      )}
                    </td>
                    <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>
                      {fmtPx(p.entry_px)}
                      {h && h.adds_count >= 2 && (
                        <div className="text-[9px] rd-sans whitespace-nowrap" style={{ color: 'var(--faint)' }}
                          title="Opening fills in the visible streak (merged bursts count once).">
                          built over {h.adds_count} adds · first fill ~{humanPx(h.first_fill_px)}
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{fmtPx(p.mark_px)}</td>
                    <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{p.leverage}x</td>
                    <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--red)' }}>{fmtPx(p.liquidation_px)}</td>
                    <td className="py-2.5 px-2 tabular-nums font-semibold" style={{ color: p.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{formatCompact(p.unrealized_pnl)}</td>
                    <td className="py-2.5 px-2 tabular-nums whitespace-nowrap" style={{ color: 'var(--dim)' }}>
                      {(() => { const l = openedDateLabel(p.opened_at, p.opened_before, p.opened_at_source); return <span title={l.tip}>{l.text}</span>; })()}
                    </td>
                    <td className="py-2.5 px-2" style={{ color: 'var(--dim)' }}><OpenDurationOrBound openedAt={p.opened_at} openedBefore={p.opened_before} pending={p.opened_at_pending} source={p.opened_at_source} compact /></td>
                    <td className="py-2.5 px-2">
                      {p.has_tpsl ? (
                        <div>
                          {p.tpsl_orders.slice(0, 2).map((o, j) => <HlOrderRow key={j} o={o} />)}
                          {p.tpsl_orders.length > 2 && <span className="text-[10px]" style={{ color: 'var(--faint)' }}>+{p.tpsl_orders.length - 2} more</span>}
                        </div>
                      ) : (
                        <span className="text-[10.5px] whitespace-nowrap" style={{ color: 'var(--faint)' }}>No on-chain TP/SL</span>
                      )}
                    </td>
                    <td className="py-2.5 px-2 tabular-nums" style={{ color: 'var(--dim)' }}>{p.rr != null ? p.rr + ':1' : '—'}</td>
                    <td className="py-2.5 px-2">
                      {!unmapped && (
                        <button onClick={() => onCopy(p)} className="rd-btn-primary text-[11px] px-2.5 py-1">Copy</button>
                      )}
                    </td>
                  </tr>
                  {expanded && h && (
                    <tr>
                      <td colSpan={13} className="px-2">
                        <HistoryDrawer h={h} ageSec={hist?.fills_age_sec ?? null} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {adds.length > 0 && (
        <div>
          <div className="text-[9.5px] font-bold uppercase tracking-[1px] mb-1" style={{ color: 'var(--faint)' }}>Resting adds ({adds.length})</div>
          <div className="rounded-[10px] px-3 py-2" style={{ background: 'var(--surface-2)' }}>
            {adds.slice(0, MAX_ROWS).map((o, i) => <HlOrderRow key={i} o={o} />)}
            {adds.length > MAX_ROWS && <div className="text-[10px] mt-1" style={{ color: 'var(--faint)' }}>+{adds.length - MAX_ROWS} more resting orders</div>}
          </div>
        </div>
      )}

      {pending_entries.length > 0 && (
        <div>
          <div className="text-[9.5px] font-bold uppercase tracking-[1px] mb-1" style={{ color: 'var(--faint)' }}>Pending entries ({pending_entries.length})</div>
          <div className="rounded-[10px] px-3 py-2" style={{ background: 'var(--surface-2)' }}>
            {pending_entries.slice(0, MAX_ROWS).map((o, i) => <HlOrderRow key={i} o={o} />)}
            {pending_entries.length > MAX_ROWS && <div className="text-[10px] mt-1" style={{ color: 'var(--faint)' }}>+{pending_entries.length - MAX_ROWS} more</div>}
          </div>
        </div>
      )}
    </div>
  );
}

