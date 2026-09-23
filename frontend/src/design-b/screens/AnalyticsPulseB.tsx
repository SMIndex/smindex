import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getPulse, type PulseTile } from '@/lib/analyticsApi';
import { humanUsd } from '@/lib/analyticsCopy';

// Design B Analytics — Pulse table (spec §3.4). SAME data as shell A's
// AnalyticsPulse (GET /api/analytics/pulse, query key ['analytics-pulse']),
// rendered as the prototype's ranked table. Presentation only: no number is
// recomputed differently; row click navigates to the SAME asset route.

function relAge(iso: string | null): string {
  if (!iso) return 'not available';
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z')) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

type Lean = 'long' | 'short' | 'flat';
const leanOf = (t: PulseTile): Lean =>
  t.stance === 'net_long' ? 'long' : t.stance === 'net_short' ? 'short' : 'flat';

export default function AnalyticsPulseB() {
  const navigate = useNavigate();
  const [f, setF] = useState<'all' | 'long' | 'short'>('all');
  const q = useQuery({ queryKey: ['analytics-pulse'], queryFn: getPulse, staleTime: 30_000, refetchInterval: 60_000 });
  const d = q.data;
  const tiles = d?.tiles ?? [];

  const sorted = [...tiles].sort((a, b) => b.smi - a.smi);
  const rows = sorted.filter((t) => f === 'all' || leanOf(t) === f);
  const nl = tiles.filter((t) => leanOf(t) === 'long').length;
  const ns = tiles.filter((t) => leanOf(t) === 'short').length;
  const nb = tiles.filter((t) => leanOf(t) === 'flat').length;
  const top = sorted[0];

  return (
    <div className="screen">
      <div className="head">
        <div>
          <h1>Analytics</h1>
          <p>Every asset the cohort holds, ranked by Smart Money Index. Click an asset for the full breakdown.</p>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div className="seg">
            {(['all', 'long', 'short'] as const).map((k) => (
              <button key={k} aria-pressed={f === k} onClick={() => setF(k)}>
                {k === 'all' ? 'All' : k === 'long' ? 'Net long' : 'Net short'}
              </button>
            ))}
          </div>
          <div className="stamp"><i />{d ? relAge(d.computed_at) : 'loading'}</div>
        </div>
      </div>

      {d?.calibrating && (
        <div className="tag amber" style={{ marginBottom: 12 }}>
          Smart Money Index is calibrating — trailing components need 48h of history ({d.history_hours}h collected).
        </div>
      )}

      <div className="summary">
        <span><b>{rows.length}</b> assets shown of {tiles.length}</span>
        <span><b className="long-c">{nl}</b> net long</span>
        <span><b className="short-c">{ns}</b> net short</span>
        <span><b>{nb}</b> balanced</span>
        {top && <span>Highest score <b>{top.asset} {top.smi.toFixed(1)}</b></span>}
      </div>

      <div className="ptable">
        {rows.length === 0 ? (
          <div className="empty" style={{ margin: 16 }}>{q.isLoading ? 'Loading assets' : 'No assets match this filter'}</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th className="n">#</th><th>Asset</th><th>Smart Money Index</th><th>Lean</th>
                <th className="r">Net position</th><th className="r">Open interest</th><th className="r">Flow 24h</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, i) => {
                const lean = leanOf(t);
                return (
                  <tr key={t.asset} style={{ cursor: 'pointer' }} onClick={() => navigate(`/analytics/${t.asset}`)}>
                    <td className="n">{i + 1}</td>
                    <td className="sym">{t.asset}{t.low_sample && <span className="dim" style={{ fontWeight: 400, fontSize: 12 }}> · low sample</span>}</td>
                    <td>
                      <div className="score">
                        <b>{t.smi.toFixed(0)}</b>
                        <div className="bar"><i className={t.smi >= 65 ? 'hi' : ''} style={{ width: `${Math.max(0, Math.min(100, t.smi))}%` }} /></div>
                      </div>
                    </td>
                    <td>
                      <span className={`lean ${lean}`}><i />{lean === 'long' ? 'Net long' : lean === 'short' ? 'Net short' : 'Balanced'}</span>
                    </td>
                    <td className="r amt">
                      {t.net_notional === 0 ? <span className="dim">Even</span> : `${t.net_notional > 0 ? '' : '−'}${humanUsd(t.net_notional)}`}
                    </td>
                    <td className="r">{humanUsd(t.cohort_oi)}</td>
                    <td className={`r ${t.flow_dir_24h > 0 ? 'long-c' : t.flow_dir_24h < 0 ? 'short-c' : 'dim'}`}>
                      {t.flow_dir_24h > 0 ? 'Inflow' : t.flow_dir_24h < 0 ? 'Outflow' : 'Flat'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {tiles.length > 0 && (
        <p className="dim" style={{ marginTop: 14, fontSize: 13 }}>Showing {rows.length} of {tiles.length} assets.</p>
      )}
    </div>
  );
}
