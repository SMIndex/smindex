import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import TraderProfileModal from '@/components/copy/TraderProfileModal';
import { getMovers, MoverCard as MoverCardT, ChurnRow } from '@/lib/analyticsApi';
import { moverSummary, humanUsd, emptyChurn, qualityStatLine, flipRating } from '@/lib/analyticsCopy';
import {
  PANEL, FreshnessLabel as Freshness, InfoTip, ConvictionBar,
} from '@/components/analytics/shared';
import { shortenAddress, formatTimeAgo } from '@/lib/formatters';

// "Who moved" page (Design Guide B4): mover cards by |net notional change|
// over the window, plus New entrants / Full exits columns. Card tap opens
// the existing wallet profile modal (HL exchange scope).

const WINDOWS = ['1h', '4h', '24h', '7d'] as const;

function PersonLine({ p }: { p: { wallet: string; rank: number | null; display_name: string | null;
                                  win_rate_7d: number | null; trades_7d: number | null;
                                  profit_factor?: number | null; max_drawdown_7d?: number | null;
                                  max_drawdown_pct_7d?: number | null; is_mm: boolean } }) {
  // Tier-2 D2: QS1 line — win rate never renders without PF once PF exists
  const qs = qualityStatLine(p.win_rate_7d, p.trades_7d, p.profit_factor ?? null,
    p.max_drawdown_pct_7d ?? null, p.max_drawdown_7d ?? null);
  return (
    <span className="flex items-center gap-1.5 min-w-0">
      {p.rank != null && <span className="text-[10px] shrink-0" style={{ color: 'var(--faint)' }}>#{p.rank}</span>}
      <span className="rd-mono truncate" style={{ color: 'var(--text)' }} title={p.wallet}>
        {p.display_name || shortenAddress(p.wallet)}
      </span>
      {qs && (
        <span className="text-[9.5px] px-1 rounded shrink-0"
              title="7d win rate (fill-stats sampling) · PF = gross wins ÷ gross losses (∞ = no losses) · maxDD = peak-to-trough of the realized-PnL path (% of account value when known)."
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          {qs}
        </span>
      )}
      {p.is_mm && (
        <span className="text-[9.5px] px-1 rounded shrink-0" title="Flagged as a likely market maker — shown here because the movers list is cohort-wide."
              style={{ border: '1px solid var(--border)', color: 'var(--faint)' }}>
          MM · shown by filter
        </span>
      )}
    </span>
  );
}

function MoverRow({ m, onOpen }: { m: MoverCardT; onOpen: (w: string) => void }) {
  const dirColor = m.net_delta >= 0 ? 'var(--green)' : 'var(--red)';
  return (
    <button onClick={() => onOpen(m.wallet)}
            className="w-full text-left rounded-[10px] px-3 py-2 hover:bg-[var(--surface-2)]"
            style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-center gap-2 text-[12px]">
        <PersonLine p={m} />
        <span className="ml-auto rd-mono tabular-nums font-semibold text-right shrink-0" style={{ color: dirColor }}>
          {moverSummary(m.kind, m.asset, m.side, Math.abs(m.net_delta))}
        </span>
        {m.kind === 'flipped' && (
          <span className="text-[9.5px] px-1 rounded shrink-0"
                title="FLIP prominence is weighted by flip accuracy at n ≥ 10 (share of past FLIPs followed by a ≥0.5% move in the flipped direction within 24h, vs the stored price history); below n=10 it ranks unweighted."
                style={{ border: '1px solid var(--border)', color: 'var(--faint)' }}>
            {flipRating(m.flip_accuracy ?? null, m.flip_n ?? null)}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1">
        {m.conviction != null
          ? <div className="flex-1"><ConvictionBar conviction={m.conviction} isMm={m.is_mm} /></div>
          : <span className="text-[10px]" style={{ color: 'var(--faint)' }}
                  title="No persisted account value for this wallet yet — conviction is never invented.">
              conviction unknown
            </span>}
        {m.last_at && (
          <span className="text-[10px] ml-auto shrink-0" style={{ color: 'var(--faint)' }}>
            {formatTimeAgo(new Date(m.last_at.endsWith('Z') ? m.last_at : m.last_at + 'Z').getTime())}
          </span>
        )}
      </div>
    </button>
  );
}

function ChurnColumn({ title, rows, kind }: { title: string; rows: ChurnRow[]; kind: 'entrants' | 'exits' }) {
  return (
    <div className="rounded-[14px] p-3" style={PANEL}>
      <div className="text-[11px] font-bold mb-2" style={{ color: 'var(--text)' }}>{title}</div>
      <div className="flex flex-col gap-1">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-1.5 text-[11.5px] px-1.5 py-1 rounded-[7px]"
               style={{ background: 'var(--surface-2)' }}>
            <PersonLine p={r} />
            <span className="ml-auto shrink-0" style={{ color: 'var(--dim)' }}>
              — <span style={{ color: r.side === 'long' ? 'var(--green)' : 'var(--red)' }}>{r.side}</span>
              <span className="rd-mono tabular-nums"> {r.asset} {humanUsd(r.notional)}</span>
            </span>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="text-[11px] py-3 text-center" style={{ color: 'var(--faint)' }}>{emptyChurn(kind)}</div>
        )}
      </div>
    </div>
  );
}

export default function AnalyticsMoversPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [window_, setWindow] = useState<string>(params.get('window') || '24h');
  const asset = params.get('asset') || undefined;
  const { isAuthenticated } = useAuth();
  const watchlist = useWatchlist(isAuthenticated);
  const subs = useSubscriptions(isAuthenticated);
  const [profileWallet, setProfileWallet] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ['analytics-movers', window_, asset],
    queryFn: () => getMovers({ window: window_, asset }),
    staleTime: 60_000, refetchInterval: 120_000,
  });
  const d = q.data;

  const onWatch = async (w: string) => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(w, 'hl')) await watchlist.unwatch(w, 'hl');
    else await watchlist.watch(w, 'hl');
  };

  return (
    <div className="rd-sans max-w-[900px] mx-auto pb-10">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button onClick={() => navigate('/analytics')} className="text-[12px] px-2 py-1 rounded-[7px] hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--dim)', border: '1px solid var(--border)' }}>← Pulse</button>
        <h1 className="text-[20px] font-bold" style={{ color: 'var(--text)' }}>
          Who moved · {window_}{asset ? ` · ${asset}` : ''}
        </h1>
        <span className="text-[11px] px-2 py-0.5 rounded-full"
              title="Cohort-wide flow-event aggregation — top leaderboard traders tracked by the sweep."
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          Top-{d?.cohort.size ?? '—'} tracked
        </span>
        <div className="rd-seg flex rounded-[8px] p-0.5" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          {WINDOWS.map((w) => (
            <button key={w} onClick={() => setWindow(w)} className="px-2 py-0.5 rounded-[6px] text-[11px]"
                    style={window_ === w ? { background: 'var(--accent)', color: '#fff' } : { color: 'var(--dim)' }}>
              {w}
            </button>
          ))}
        </div>
        <div className="ml-auto"><Freshness computedAt={d?.computed_at ?? null} label="flow events · mixed resolution" /></div>
      </div>

      <div className="rounded-[14px] p-4 mb-4" style={PANEL}>
        <div className="flex items-center gap-2 mb-2">
          <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Biggest moves</h2>
          <InfoTip text="Per (wallet, asset): sum of detected notional changes in the window, largest absolute first, max 10. Move kind: FLIP beats OPEN beats CLOSE; otherwise grew/cut by sign. Tap a card for the wallet profile." />
        </div>
        <div className="flex flex-col gap-1.5">
          {(d?.movers || []).map((m, i) => <MoverRow key={i} m={m} onOpen={setProfileWallet} />)}
          {d && d.movers.length === 0 && (
            <div className="text-[11.5px] py-4 text-center" style={{ color: 'var(--faint)' }}>
              No cohort moves detected in this window.
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <ChurnColumn title={`New entrants · ${window_}`} rows={d?.entrants || []} kind="entrants" />
        <ChurnColumn title={`Full exits · ${window_}`} rows={d?.exits || []} kind="exits" />
      </div>

      {q.isLoading && (
        <div className="rounded-[14px] h-56 animate-pulse mt-4" style={{ background: 'var(--surface-2)' }} />
      )}

      {profileWallet && (
        <TraderProfileModal
          wallet={profileWallet}
          exchange="hl"
          isWatched={watchlist.isWatched(profileWallet, 'hl')}
          onWatchToggle={onWatch}
          onCreateSub={subs.create}
          onClose={() => setProfileWallet(null)}
        />
      )}
    </div>
  );
}
