import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { shortenAddress, formatUSD } from '@/lib/formatters';
import {
  listAutoSubs, updateAutoSub, stopAutoSub, pauseAllAuto, getAutoLog, getAutoPositions, type AutoCopySub,
} from '@/lib/copyAutoApi';

// Copy dashboard -> Auto-copy tab (spec 2.11): one card per subscription, the
// live decision log (shadow rows tagged), open copied positions, Pause all.

function SubCard({ s, onChanged }: { s: AutoCopySub; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setErr(null);
    try { await fn(); onChanged(); } catch (e: any) { setErr(e?.response?.data?.detail || 'Failed'); } finally { setBusy(false); }
  };
  return (
    <div className="rounded-xl border border-text-secondary/10 bg-bg-secondary/40 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="font-semibold text-sm text-text-primary">{shortenAddress(s.leader_wallet)}</div>
        <div className="flex gap-1.5">
          <span className={clsx('text-[10px] px-2 py-0.5 rounded-full border', s.mode === 'live' ? 'bg-danger/10 text-danger border-danger/30' : 'bg-accent/10 text-accent border-accent/20')}>
            {s.mode === 'live' ? 'LIVE' : 'shadow'}
          </span>
          <span className={clsx('text-[10px] px-2 py-0.5 rounded-full border', s.status === 'active' ? 'bg-success/10 text-success border-success/30' : 'bg-warning/10 text-warning border-warning/30')}>
            {s.status}
          </span>
        </div>
      </div>
      {s.pause_reason && <div className="text-[11px] text-warning">Paused: {s.pause_reason}</div>}
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div><div className="text-text-secondary/70">P/L today</div><div className={s.pnl_today >= 0 ? 'text-success' : 'text-danger'}>{formatUSD(s.pnl_today)}</div></div>
        <div><div className="text-text-secondary/70">P/L total</div><div className={s.pnl_total >= 0 ? 'text-success' : 'text-danger'}>{formatUSD(s.pnl_total)}</div></div>
        <div><div className="text-text-secondary/70">Open</div><div className="text-text-primary">{s.open_positions}</div></div>
      </div>
      <div className="text-[11px] text-text-secondary">{s.summary}</div>
      {s.last_action && (
        <div className="text-[11px] text-text-secondary">
          Last: <span className="text-text-primary">{s.last_action.decision}</span> {s.last_action.event} {s.last_action.coin}
          {s.last_action.reason ? ` — ${s.last_action.reason}` : ''} · {new Date(s.last_action.at + 'Z').toLocaleTimeString()}
        </div>
      )}
      {err && <div className="text-[11px] text-danger">{err}</div>}
      <div className="flex flex-wrap gap-2">
        <button disabled={busy} onClick={() => act(() => updateAutoSub(s.id, { status: s.status === 'active' ? 'paused' : 'active' }))}
                className="text-xs px-3 py-2 rounded-lg border border-text-secondary/20 min-h-[44px]">
          {s.status === 'active' ? 'Pause' : 'Resume'}
        </button>
        <button disabled={busy} onClick={() => act(() => updateAutoSub(s.id, { mode: s.mode === 'live' ? 'shadow' : 'live' }))}
                className={clsx('text-xs px-3 py-2 rounded-lg border min-h-[44px]', s.mode === 'live' ? 'border-accent/30 text-accent' : 'border-danger/30 text-danger')}>
          {s.mode === 'live' ? 'Switch to Shadow' : 'Switch to Live'}
        </button>
        <button disabled={busy} onClick={() => act(() => stopAutoSub(s.id))}
                className="text-xs px-3 py-2 rounded-lg border border-text-secondary/20 text-text-secondary min-h-[44px]">
          Stop
        </button>
      </div>
    </div>
  );
}

export default function AutoCopyTab() {
  const qc = useQueryClient();
  const subs = useQuery({ queryKey: ['copy-auto-subs'], queryFn: listAutoSubs, refetchInterval: 15000 });
  const log = useQuery({ queryKey: ['copy-auto-log'], queryFn: () => getAutoLog(100), refetchInterval: 10000 });
  const pos = useQuery({ queryKey: ['copy-auto-positions'], queryFn: getAutoPositions, refetchInterval: 15000 });
  const [pausing, setPausing] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ['copy-auto-subs'] }); qc.invalidateQueries({ queryKey: ['copy-auto-log'] }); };
  const pauseAll = async () => { setPausing(true); try { await pauseAllAuto(); refresh(); } finally { setPausing(false); } };
  const open = (pos.data ?? []).filter((p) => p.status === 'open');

  return (
    <div className="space-y-4">
      <div className="sticky top-0 z-10 flex items-center justify-between gap-2 py-2 bg-bg-primary/90 backdrop-blur">
        <div className="text-sm font-semibold text-text-primary">Auto-copy</div>
        <button onClick={pauseAll} disabled={pausing || !(subs.data ?? []).some((s) => s.status === 'active')}
                className="text-xs font-semibold px-3 py-2 rounded-lg bg-danger/15 text-danger border border-danger/30 min-h-[44px] disabled:opacity-50">
          {pausing ? 'Pausing…' : 'Pause all auto-copy'}
        </button>
      </div>

      {subs.isLoading && <div className="text-xs text-text-secondary">Loading…</div>}
      {subs.data && subs.data.length === 0 && (
        <div className="text-xs text-text-secondary">No auto-copy subscriptions. Use “Auto-copy” on a trader’s profile or Discover card.</div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {(subs.data ?? []).map((s) => <SubCard key={s.id} s={s} onChanged={refresh} />)}
      </div>

      {open.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-text-secondary mb-1">Open copied positions</div>
          <div className="space-y-1">
            {open.map((p) => (
              <div key={p.id} className="flex items-center justify-between text-[12px] rounded-lg border border-text-secondary/10 px-3 py-2">
                <span className="text-text-primary">{p.coin} <span className={p.side === 'long' ? 'text-success' : 'text-danger'}>{p.side}</span> {p.size} @ {Number(p.entry_px).toPrecision(6)}</span>
                <span className="text-text-secondary">{p.mode === 'shadow' ? 'shadow · ' : ''}{p.leverage}x{p.sl_px ? ` · SL ${Number(p.sl_px).toPrecision(6)}` : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="text-xs font-semibold text-text-secondary mb-1">Every leader event and what we did</div>
        <div className="rounded-lg border border-text-secondary/10 divide-y divide-text-secondary/10 max-h-[480px] overflow-y-auto">
          {(log.data ?? []).map((r) => (
            <div key={r.id} className="px-3 py-2 text-[12px] flex items-start justify-between gap-3">
              <div className="min-w-0">
                <span className={clsx('font-semibold', r.decision === 'copied' ? 'text-success' : r.decision === 'failed' ? 'text-danger' : r.decision === 'shadow' ? 'text-accent' : 'text-text-secondary')}>
                  {r.decision}
                </span>{' '}
                {r.mode === 'shadow' && <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent mr-1">shadow</span>}
                <span className="text-text-primary">{r.event_type} {r.coin} {r.side}</span>
                <span className="text-text-secondary"> · {shortenAddress(r.leader_wallet)}</span>
                {r.reason && <div className="text-[11px] text-text-secondary truncate">{r.gate ? `${r.gate}: ` : ''}{r.reason}</div>}
              </div>
              <span className="text-[11px] text-text-secondary whitespace-nowrap">{new Date(r.created_at + 'Z').toLocaleTimeString()}</span>
            </div>
          ))}
          {(log.data ?? []).length === 0 && <div className="px-3 py-3 text-xs text-text-secondary">No decisions yet.</div>}
        </div>
      </div>
    </div>
  );
}
