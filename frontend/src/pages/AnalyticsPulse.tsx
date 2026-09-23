import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getPulse, getSmi, PulseTile } from '@/lib/analyticsApi';
import {
  humanUsd, tileCaption, divergenceSentence, divergenceNone, rotationRead,
} from '@/lib/analyticsCopy';
import {
  PANEL, FreshnessLabel as Freshness, InfoTip, FlowRow,
} from '@/components/analytics/shared';

// Market Pulse — the Analytics landing (Design Guide B2), verdict-first:
// tile tint = cohort stance (teal long / pink short / neutral balanced),
// low-sample tiles per A5 (grey, NO score, no tint), SMI number tap-through
// to its five components (B2b — no black boxes). All prose from the tested
// copy module (A8).

const TILE_LIMIT = 23;   // + the "+N more" overflow tile = 24 = 8 rows of 3

function tileStyle(t: PulseTile): React.CSSProperties {
  if (t.low_sample) return { background: 'var(--surface-2)', border: '1px solid var(--border)' };
  if (t.stance === 'net_long')
    return { background: 'var(--green-soft)', border: '1px solid rgba(61,220,132,.3)' };
  if (t.stance === 'net_short')
    return { background: 'var(--red-soft)', border: '1px solid rgba(255,84,112,.3)' };
  return { background: 'var(--surface-2)', border: '1px solid var(--border)' };
}

function flowLabel(t: PulseTile): { text: string; color: string } {
  if (t.low_sample) return { text: 'thin', color: 'var(--faint)' };
  if (t.flow_dir_24h > 0) return { text: '↗ flow 24h', color: 'var(--green)' };
  if (t.flow_dir_24h < 0) return { text: '↘ flow 24h', color: 'var(--red)' };
  return { text: 'flat flow', color: 'var(--faint)' };
}

// B2b — SMI breakdown without leaving the page (portal-free inline overlay
// matching the terminal's modal pattern)
function SmiBreakdown({ asset, onClose }: { asset: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['analytics-smi', asset], queryFn: () => getSmi(asset),
                       staleTime: 60_000 });
  const d = q.data;
  const latest = d?.latest;
  const ROWS = [
    ['Positioning skew', 'c1', 'skew'], ['Flow direction', 'c2', 'flow'],
    ['Breadth', 'c3', 'breadth'], ['Leverage appetite', 'c4', 'leverage'],
    ['Divergence', 'c5', 'divergence'],
  ] as const;
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4" role="dialog" aria-label={`${asset} SMI breakdown`}>
      <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,.5)' }} onClick={onClose} />
      <div className="relative rounded-[14px] p-4 w-full max-w-[440px] rd-sans" style={PANEL}>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>{asset} — Smart Money Index</span>
          {d?.calibrating && (
            <span className="text-[9.5px] px-1.5 py-0.5 rounded" title={`${d.history_hours}h of 48h history collected.`}
                  style={{ background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--dim)' }}>
              calibrating
            </span>
          )}
          <button onClick={onClose} className="ml-auto text-[13px] px-2" style={{ color: 'var(--dim)' }}>✕</button>
        </div>
        {!latest && <div className="text-[11.5px] py-4" style={{ color: 'var(--faint)' }}>
          {q.isLoading ? 'loading…' : 'No SMI history for this asset yet.'}</div>}
        {latest && (
          <>
            <div className="rd-mono text-[30px] font-extrabold tabular-nums mb-2"
                 style={{ color: latest.smi >= 52 ? 'var(--green)' : latest.smi <= 48 ? 'var(--red)' : 'var(--text)' }}>
              {latest.smi.toFixed(1)}<span className="text-[12px] font-normal" style={{ color: 'var(--faint)' }}> /100 SMI</span>
            </div>
            <div className="flex flex-col gap-1.5">
              {ROWS.map(([label, key, wkey]) => (
                <div key={key} className="flex items-center gap-2 text-[11px]">
                  <span className="w-[110px] shrink-0" style={{ color: 'var(--faint)' }}>{label}</span>
                  <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                    <div className="h-full rounded-full" style={{ width: `${latest.components[key]}%`,
                         background: latest.components[key] >= 52 ? 'var(--green)' : latest.components[key] <= 48 ? 'var(--red)' : 'var(--dim)' }} />
                  </div>
                  <span className="rd-mono tabular-nums w-9 text-right" style={{ color: 'var(--text)' }}>
                    {latest.components[key].toFixed(0)}
                  </span>
                  <span className="rd-mono tabular-nums w-12 text-right text-[9.5px]" style={{ color: 'var(--faint)' }}>
                    w {(d!.weights as Record<string, number>)[wkey] * 100}%
                  </span>
                </div>
              ))}
            </div>
            <details className="mt-2">
              <summary className="text-[10.5px] cursor-pointer" style={{ color: 'var(--faint)' }}>raw inputs (audit)</summary>
              <pre className="text-[9.5px] mt-1 p-2 rounded-[8px] overflow-x-auto rd-scroll"
                   style={{ background: 'var(--surface-2)', color: 'var(--dim)' }}>
                {JSON.stringify(latest.inputs, null, 1)}
              </pre>
            </details>
            <div className="text-[10px] mt-2" style={{ color: 'var(--faint)' }}>
              Per 20-minute sweep cycle · CORE cohort (MMs excluded) · formulas in services/analytics/smi.py.
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function AnalyticsPulsePage() {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ['analytics-pulse'], queryFn: getPulse,
                       staleTime: 60_000, refetchInterval: 120_000 });
  const d = q.data;
  const [showAll, setShowAll] = useState(false);
  const [smiAsset, setSmiAsset] = useState<string | null>(null);
  const rot = d?.risk_rotation;
  const rotSeries = (rot?.series || []).filter((p) => p.majors_share != null);
  const rotStart = rotSeries[0]?.majors_share ?? null;
  const rotNow = rot?.now ?? null;
  const tiles = d?.tiles || [];
  const shownTiles = showAll ? tiles : tiles.slice(0, TILE_LIMIT);
  const strongestDivergence = d?.divergences?.[0] ?? null;

  return (
    <div className="rd-sans max-w-[1100px] mx-auto pb-10">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <h1 className="text-[20px] font-bold" style={{ color: 'var(--text)' }}>Market pulse</h1>
        <span className="text-[11px] px-2 py-0.5 rounded-full"
              title="Every number measures the tracked CORE cohort (top leaderboard traders, MMs excluded) — not the whole market."
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          Top-{d?.cohort.size ?? '—'} tracked · MMs excluded
        </span>
        <button onClick={() => navigate('/analytics/movers')} className="text-[12px] px-2 py-1 rounded-[7px] hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--dim)', border: '1px solid var(--border)' }}>Who moved →</button>
        {d?.calibrating && (
          <span className="text-[10px] px-2 py-0.5 rounded"
                title={`SMI needs 48h of history for its trailing components; ${d.history_hours}h collected so far.`}
                style={{ background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--dim)' }}>
            SMI calibrating — {d.history_hours}h / 48h history
          </span>
        )}
        <div className="ml-auto"><Freshness computedAt={d?.computed_at ?? null} label="full-cohort sweep" /></div>
      </div>

      {/* SMI heatmap grid — B2.2: 3-col desktop / 2-col mobile, OI-ordered */}
      <div className="rounded-[14px] p-4 mb-1" style={PANEL}>
        <div className="flex items-center gap-2 mb-2">
          <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Smart Money Index heatmap</h2>
          <InfoTip text="Tile tint = cohort stance (teal net long, pink net short, neutral balanced). Big number = SMI 0-100. Arrow = 24h net flow direction. Tap a tile for the asset page; tap the score for its five components." />
          <div className="ml-auto"><Freshness computedAt={d?.computed_at ?? null} label="full-cohort sweep" /></div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {shownTiles.map((t) => {
            const fl = flowLabel(t);
            return (
              <div key={t.asset} role="button" tabIndex={0}
                   onClick={() => navigate(`/analytics/${t.asset}`)}
                   className="rounded-[10px] p-2.5 cursor-pointer transition-transform hover:scale-[1.02]"
                   style={tileStyle(t)}
                   title={t.low_sample
                     ? `${t.asset}: low sample (${t.wallets} wallets) — no score claimed`
                     : `${t.asset}: SMI ${t.smi.toFixed(1)}${d?.calibrating ? ' (calibrating)' : ''}${t.smi_delta_24h != null ? `, 24h Δ ${t.smi_delta_24h > 0 ? '+' : ''}${t.smi_delta_24h.toFixed(1)}` : ''} · ${tileCaption(t.stance, t.net_notional, t.cohort_oi)}`}>
                <div className="flex items-center gap-1">
                  <span className="rd-mono font-bold text-[12px]" style={{ color: 'var(--text)' }}>{t.asset}</span>
                  <span className="ml-auto text-[9.5px]" style={{ color: fl.color }}>{fl.text}</span>
                </div>
                {t.low_sample ? (
                  <div className="text-[11px] py-1.5" style={{ color: 'var(--faint)' }}>
                    low sample: {t.wallets} wallets
                  </div>
                ) : (
                  <button onClick={(e) => { e.stopPropagation(); setSmiAsset(t.asset); }}
                          title="Tap for the five SMI components (no black boxes)"
                          className="rd-mono text-[19px] font-extrabold tabular-nums hover:underline"
                          style={{ color: 'var(--text)' }}>
                    {t.smi.toFixed(0)}
                    <span className="text-[9px] font-normal" style={{ color: 'var(--faint)' }}> /100 SMI</span>
                    {d?.calibrating && <span className="text-[8px] font-normal align-super" style={{ color: 'var(--faint)' }}>~</span>}
                  </button>
                )}
                <div className="text-[9.5px]" style={{ color: 'var(--faint)' }}>
                  {t.low_sample ? `OI ${humanUsd(t.cohort_oi)}` : tileCaption(t.stance, t.net_notional, t.cohort_oi)}
                </div>
              </div>
            );
          })}
          {!showAll && tiles.length > TILE_LIMIT && (
            <button onClick={() => setShowAll(true)}
                    className="rounded-[10px] p-2.5 text-[12px] font-semibold"
                    style={{ background: 'var(--surface-2)', border: '1px dashed var(--border-strong)', color: 'var(--dim)' }}>
              +{tiles.length - TILE_LIMIT} more assets
            </button>
          )}
        </div>
        {d && tiles.length === 0 && (
          <div className="text-[11px] py-6 text-center" style={{ color: 'var(--faint)' }}>
            SMI tiles appear after the first sweep cycle scores assets.
          </div>
        )}
      </div>
      <div className="text-[10px] mb-4" style={{ color: 'var(--faint)' }}>
        SMI 0–100 · tap the score on any tile to see its five components — no black boxes.
        {d?.calibrating && ` ~ calibrating: ${d.history_hours}h / 48h of trailing history collected — treat direction, not magnitude.`}
      </div>

      {/* B2.4 two-card row: movers + divergence watch */}
      {d && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
          <div className="rounded-[14px] p-3" style={PANEL}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Biggest SMI movers · 24h</span>
              <InfoTip text="Largest absolute change in Smart Money Index over the last 24h of cycles." />
            </div>
            {d.movers.length === 0 && <div className="text-[11px]" style={{ color: 'var(--faint)' }}>Appears after 24h of SMI history.</div>}
            <div className="flex flex-col gap-1">
              {d.movers.map((m) => {
                const old = m.smi - (m.smi_delta_24h ?? 0);
                return (
                  <button key={m.asset} onClick={() => navigate(`/analytics/${m.asset}`)}
                          className="flex items-center gap-2 text-[12px] px-2 py-1 rounded-[7px] hover:bg-[var(--surface-2)]">
                    <span className="rd-mono font-bold" style={{ color: 'var(--text)' }}>{m.asset}</span>
                    <span className="ml-auto rd-mono tabular-nums font-semibold"
                          style={{ color: (m.smi_delta_24h ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {old.toFixed(0)} → {m.smi.toFixed(0)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          {/* Divergence watch — ONE strongest sentence (amber), grey when none */}
          <div className="rounded-[14px] p-3"
               style={strongestDivergence
                 ? { background: 'var(--warn-bg)', border: '1px solid var(--warn-border)' }
                 : PANEL}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Divergence watch</span>
              <InfoTip text="Strongest current divergence: cohort positioning vs funding (contrarian conviction) or vs 24h price. Thresholds: |skew| ≥ 0.15, |funding| ≥ 0.1bp/h, |price move| ≥ 1%. Ranked by |skew × driver|." />
            </div>
            {strongestDivergence ? (
              <button onClick={() => navigate(`/analytics/${strongestDivergence.asset}`)}
                      className="text-left text-[12px]" style={{ color: 'var(--text)' }}
                      title={`skew ${strongestDivergence.skew}${strongestDivergence.kind === 'funding'
                        ? `, funding ${((strongestDivergence.funding_hourly ?? 0) * 100).toFixed(4)}%/h`
                        : `, 24h price ${strongestDivergence.price_change_24h}%`} — opposite signs`}>
                {divergenceSentence(strongestDivergence)}
                {d.divergences.length > 1 && (
                  <span className="text-[10px] ml-1" style={{ color: 'var(--dim)' }}>
                    (+{d.divergences.length - 1} more on asset pages)
                  </span>
                )}
              </button>
            ) : (
              <div className="text-[12px]" style={{ color: 'var(--faint)' }}>{divergenceNone()}</div>
            )}
          </div>
        </div>
      )}

      {/* second row: rotation + biggest flow + crowded trades */}
      {d && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <div className="rounded-[14px] p-3" style={PANEL}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Risk rotation</span>
              <InfoTip text={`Share of cohort notional in majors (${(rot?.majors || []).join(', ')}) vs everything else, per cycle over 7 days.`} />
            </div>
            {rotNow != null ? (
              <>
                <div className="rd-mono text-[20px] font-bold" style={{ color: 'var(--text)' }}>{rotNow.toFixed(1)}%
                  <span className="text-[11px] font-normal ml-1" style={{ color: 'var(--faint)' }}>in majors</span>
                </div>
                {rotStart != null && (
                  <div className="text-[11px] mt-0.5" style={{ color: rotNow >= rotStart ? 'var(--green)' : 'var(--red)' }}>
                    {rotNow >= rotStart ? '▲' : '▼'} {Math.abs(rotNow - rotStart).toFixed(1)} pts over {rotSeries.length} cycles
                    <span style={{ color: 'var(--faint)' }}> · {rotationRead(rotNow, rotStart)}</span>
                  </div>
                )}
              </>
            ) : <div className="text-[11px]" style={{ color: 'var(--faint)' }}>Appears after the first cycles.</div>}
          </div>
          <div className="rounded-[14px] p-3" style={PANEL}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Biggest flow today</span>
              <InfoTip text="Single largest |notional change| event across all assets since 00:00 UTC." />
            </div>
            {d.biggest_event_today ? <FlowRow e={d.biggest_event_today} />
              : <div className="text-[11px]" style={{ color: 'var(--faint)' }}>No flow events yet today.</div>}
          </div>
          <div className="rounded-[14px] p-3" style={PANEL}>
            <div className="flex items-center gap-1.5 mb-2">
              <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Crowded trades</span>
              <InfoTip text={`Same asset + same side + entries within ±${d.crowded_trades?.entry_tol_pct ?? 1}%, held by ${d.crowded_trades?.min_wallets ?? 3}+ core-cohort wallets.`} />
            </div>
            <div className="flex flex-col gap-1">
              {(d.crowded_trades?.clusters ?? []).slice(0, 3).map((c, i) => (
                <button key={i} onClick={() => navigate(`/analytics/${c.asset}`)}
                        className="flex items-center gap-1.5 text-left text-[11px] px-1.5 py-1 rounded-[7px] hover:bg-[var(--surface-2)]">
                  <span className="rd-mono font-bold" style={{ color: 'var(--text)' }}>{c.asset}</span>
                  <span style={{ color: c.side === 'long' ? 'var(--green)' : 'var(--red)' }}>{c.side}</span>
                  <span style={{ color: 'var(--dim)' }}>{c.wallet_count} wallets</span>
                  <span className="ml-auto rd-mono tabular-nums" style={{ color: 'var(--dim)' }}>{humanUsd(c.notional)}</span>
                </button>
              ))}
              {(d.crowded_trades?.clusters ?? []).length === 0 && (
                <div className="text-[11px]" style={{ color: 'var(--faint)' }}>No crowded trades this cycle.</div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* B2.5 flow highlights — max 6, view all -> movers */}
      <div className="rounded-[14px] p-4" style={PANEL}>
        <div className="flex items-center gap-2 mb-2">
          <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Flow highlights · all assets · 24h</h2>
          <InfoTip text={`FLIPs plus outsized (BIG) events — |notional change| above the 95th percentile of the trailing 7 days${d?.outsized_threshold_p95 ? ` = ${humanUsd(d.outsized_threshold_p95)}` : ''}.`} />
          <button onClick={() => navigate('/analytics/movers')} className="ml-auto text-[11px] px-2 py-0.5 rounded-[7px] hover:bg-[var(--surface-2)]"
                  style={{ color: 'var(--accent-2)' }}>view all →</button>
        </div>
        <div className="flex flex-col gap-1">
          {(d?.highlights || []).slice(0, 6).map((e, i) => <FlowRow key={i} e={e} />)}
          {d && d.highlights.length === 0 && (
            <div className="text-[11px] py-4 text-center" style={{ color: 'var(--faint)' }}>
              No FLIPs or outsized events in the last 24h.
            </div>
          )}
        </div>
      </div>

      {q.isLoading && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
          {[0, 1].map((i) => <div key={i} className="rounded-[14px] h-56 animate-pulse" style={{ background: 'var(--surface-2)' }} />)}
        </div>
      )}

      {smiAsset && <SmiBreakdown asset={smiAsset} onClose={() => setSmiAsset(null)} />}
    </div>
  );
}
