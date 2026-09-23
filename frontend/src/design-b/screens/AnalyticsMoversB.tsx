import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getMovers, type MoverCard, type ChurnRow } from '@/lib/analyticsApi';
import { humanUsd } from '@/lib/analyticsCopy';

// Design B "Who moved" (step-3 fix, spec omission). SAME query as shell A's
// AnalyticsMovers (getMovers, key ['analytics-movers', window, asset]),
// presentation only, styled as tables in the same grammar as the per-asset
// "Recent moves" table. Row tap opens the trader profile (route unchanged).

function relAge(iso: string | null): string {
  if (!iso) return 'not available';
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z')) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
// conviction: under 1% renders "<1%", never "0%" (step-3 fix #3)
export function convPct(pct: number): string {
  return pct > 0 && pct < 1 ? '<1%' : `${pct.toFixed(0)}%`;
}
function shorten(w: string): string { return w.length > 12 ? `${w.slice(0, 6)}…${w.slice(-4)}` : w; }

function moverAction(kind: MoverCard['kind'], side: string): { text: string; cls: 'long' | 'short' | 'flat' } {
  const long = side === 'long';
  if (kind === 'opened') return { text: `Opened ${side}`, cls: long ? 'long' : 'short' };
  if (kind === 'grew') return { text: `Added to ${side}`, cls: long ? 'long' : 'short' };
  if (kind === 'cut') return { text: `Trimmed ${side}`, cls: 'flat' };
  if (kind === 'closed') return { text: `Closed ${side}`, cls: 'flat' };
  return { text: `Flipped to ${side}`, cls: long ? 'long' : 'short' };
}

const WINDOWS = ['1h', '4h', '24h', '7d'] as const;

export default function AnalyticsMoversB() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const asset = params.get('asset') || undefined;
  const [window_, setWindow] = useState<string>(params.get('window') || '24h');
  const q = useQuery({ queryKey: ['analytics-movers', window_, asset], queryFn: () => getMovers({ window: window_, asset }), staleTime: 60_000, refetchInterval: 120_000 });
  const d = q.data;

  return (
    <div className="screen">
      <div className="head">
        <div>
          <button className="btn sm ghost" style={{ paddingLeft: 0, marginBottom: 4 }} onClick={() => navigate('/analytics')}>← All assets</button>
          <h1>Who moved{asset ? ` in ${asset}` : ''}</h1>
          <p>Every position change the cohort made in the window, biggest first</p>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div className="seg">
            {WINDOWS.map((w) => <button key={w} aria-pressed={window_ === w} onClick={() => setWindow(w)}>{w}</button>)}
          </div>
          <div className="stamp"><i />{d ? relAge(d.computed_at) : 'loading'}</div>
        </div>
      </div>

      <div className="card">
        <h2>Biggest moves</h2>
        <div className="sub">Per wallet and asset, by size of the net change in the window</div>
        {(d?.movers?.length ?? 0) === 0 ? (
          <div className="empty">{q.isLoading ? 'Loading moves' : 'No cohort moves in this window'}</div>
        ) : (
          <table>
            <thead><tr><th>Wallet</th><th>Asset</th><th>Action</th><th>Detail</th><th className="r">Net change</th><th className="r">When</th></tr></thead>
            <tbody>
              {(d?.movers ?? []).map((m: MoverCard, i) => {
                const a = moverAction(m.kind, m.side);
                const detail = m.conviction ? `${convPct(m.conviction.pct)} of account as margin` : (m.win_rate_7d != null ? `${(m.win_rate_7d * 100).toFixed(0)}% win rate` : '');
                return (
                  <tr key={i} style={{ cursor: 'pointer' }} onClick={() => navigate(`/copy/trader/${m.wallet}?exchange=hl`)}>
                    <td className="who mono">{m.display_name || shorten(m.wallet)}{m.rank != null ? ` #${m.rank}` : ''}</td>
                    <td className="sym" style={{ cursor: 'pointer' }} onClick={(e) => { e.stopPropagation(); navigate(`/analytics/${m.asset}`); }}>{m.asset}</td>
                    <td><span className={`tag ${a.cls}`}>{a.text}</span></td>
                    <td className="muted">{detail}</td>
                    <td className={`r amt ${m.net_delta >= 0 ? 'long-c' : 'short-c'}`}>{m.net_delta >= 0 ? '+' : '−'}{humanUsd(m.net_delta)}</td>
                    <td className="r dim">{relAge(m.last_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="row two" style={{ marginTop: 16 }}>
        <ChurnCard title="New entrants" rows={d?.entrants ?? []} kind="entrants" nav={navigate} />
        <ChurnCard title="Full exits" rows={d?.exits ?? []} kind="exits" nav={navigate} />
      </div>
    </div>
  );
}

function ChurnCard({ title, rows, kind, nav }: { title: string; rows: ChurnRow[]; kind: 'entrants' | 'exits'; nav: (to: string) => void }) {
  return (
    <div className="card">
      <h2>{title}</h2>
      <div className="sub">{kind === 'entrants' ? 'Fresh positions opened in the window' : 'Positions fully closed in the window'}</div>
      {rows.length === 0 ? (
        <div className="empty">No {kind} in this window — low churn.</div>
      ) : (
        <table>
          <thead><tr><th>Wallet</th><th>Asset</th><th>Side</th><th className="r">Size</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ cursor: 'pointer' }} onClick={() => nav(`/copy/trader/${r.wallet}?exchange=hl`)}>
                <td className="who mono">{r.display_name || shorten(r.wallet)}{r.rank != null ? ` #${r.rank}` : ''}</td>
                <td className="sym">{r.asset}</td>
                <td><span className={`tag ${r.side === 'long' ? 'long' : 'short'}`}>{r.side === 'long' ? 'Long' : 'Short'}</span></td>
                <td className="r amt">{humanUsd(r.notional)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
