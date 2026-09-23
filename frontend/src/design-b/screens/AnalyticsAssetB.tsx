import { useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getAnalyticsAssets, getAssetDetail, getSmi, getFlows,
  type MatrixFilters, type FlowEvent, type EntryBucket,
} from '@/lib/analyticsApi';
import {
  verdict, humanUsd, humanPx, squeezeTakeaway, trackRecordCollecting, trackRecordLine,
} from '@/lib/analyticsCopy';

// Design B Analytics — per-asset (spec §3.4 items 1-9). SAME queries as shell A
// (getAnalyticsAssets/getAssetDetail/getSmi/getFlows with identical params +
// query keys) and the SAME analyticsCopy templates → every number equals A for
// the same asset + filters. Presentation only. All 10 filter groups map 1:1 to
// the existing filter state (see DESIGN_B_FUNCTIONAL_INVENTORY.md table).
// Honesty labels preserved: as-of stamp, low-sample, calibrating, track-record
// collecting line, resolution, ≈ markers, conviction flags.

const ACCT_BANDS = [
  ['Any', undefined], ['Under $10K', 'lt_10k'], ['$10K to $100K', '10k_100k'],
  ['$100K to $1M', '100k_1m'], ['$1M to $10M', '1m_10m'], ['$10M+', 'gt_10m'],
] as const;
const LEV_BANDS = [['Any', undefined], ['0 to 2x', '0_2'], ['2 to 5x', '2_5'], ['5 to 10x', '5_10'], ['10x+', '10_plus']] as const;
const AGE_BANDS = [['Any', undefined], ['Under 24h', 'h24'], ['1 to 7 days', 'd1_7'], ['Over 7 days', 'd7_plus'], ['Unknown', 'undated']] as const;

function relAge(iso: string | null): string {
  if (!iso) return 'not available';
  const s = Math.max(0, (Date.now() - Date.parse(iso.endsWith('Z') ? iso : iso + 'Z')) / 1000);
  if (s < 90) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hours ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function Seg<T>({ opts, value, onPick }: { opts: readonly (readonly [string, T])[]; value: T; onPick: (v: T) => void }) {
  return (
    <div className="seg">
      {opts.map(([label, v]) => (
        <button key={label} aria-pressed={value === v} onClick={() => onPick(v as T)}>{label}</button>
      ))}
    </div>
  );
}

function flowLabel(e: FlowEvent): { text: string; cls: 'long' | 'short' | 'flat' } {
  const long = e.side === 'long';
  if (e.event_type === 'OPEN') return { text: `Opened ${e.side}`, cls: long ? 'long' : 'short' };
  if (e.event_type === 'INCREASE') return { text: `Added to ${e.side}`, cls: long ? 'long' : 'short' };
  if (e.event_type === 'REDUCE') return { text: `Trimmed ${e.side}`, cls: 'flat' };
  if (e.event_type === 'CLOSE') return { text: `Closed ${e.side}`, cls: 'flat' };
  return { text: `Flipped to ${e.side}`, cls: long ? 'long' : 'short' };
}

// mirrored entry histogram (longs up green, shorts down red) — same buckets as A
function EntrySvg({ long, short, mark }: { long: EntryBucket[]; short: EntryBucket[]; mark: number | null }) {
  const all = [...long, ...short].filter((b) => b.count > 0);
  if (!all.length) return <div className="empty">Not enough entry data yet</div>;
  const lo = Math.min(...all.map((b) => b.px_lo));
  const hi = Math.max(...all.map((b) => b.px_hi));
  const span = hi - lo || 1;
  const W = 600, H = 150, MID = 75, maxN = Math.max(...all.map((b) => b.notional), 1);
  const bar = (b: EntryBucket, up: boolean, i: number) => {
    const x = ((b.px_lo - lo) / span) * W;
    const w = Math.max(2, ((b.px_hi - b.px_lo) / span) * W - 2);
    const h = (b.notional / maxN) * (MID - 8);
    return <rect key={(up ? 'l' : 's') + i} x={x} y={up ? MID - h : MID} width={w} height={h}
      fill={up ? 'var(--long)' : 'var(--short)'} opacity={0.85} rx={1} />;
  };
  const markX = mark != null ? ((mark - lo) / span) * W : null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 150, display: 'block' }}>
      <line x1={0} x2={W} y1={MID} y2={MID} stroke="var(--line)" />
      {long.map((b, i) => bar(b, true, i))}
      {short.map((b, i) => bar(b, false, i))}
      {markX != null && <line x1={markX} x2={markX} y1={4} y2={H - 4} stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="4 3" />}
    </svg>
  );
}

function TrendSvg({ points }: { points: { t: string; net_notional: number; wallets_long: number; wallets_short: number }[] }) {
  if (points.length < 2) return <div className="empty">Positioning trend builds over the next few sweeps</div>;
  // long share proxy from net vs gross isn't in trend_48h; use wallet share as the A page does for the spark
  const shares = points.map((p) => {
    const tot = p.wallets_long + p.wallets_short;
    return tot ? (p.wallets_long / tot) * 100 : 50;
  });
  const W = 600, H = 150, lo = Math.min(...shares, 40), hi = Math.max(...shares, 60), span = hi - lo || 1;
  const path = shares.map((s, i) => `${i ? 'L' : 'M'}${(i / (shares.length - 1)) * W},${H - 10 - ((s - lo) / span) * (H - 24)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 150, display: 'block' }}>
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
    </svg>
  );
}

export default function AnalyticsAssetB() {
  const { assetParam } = useParams<{ assetParam: string }>();
  const navigate = useNavigate();
  const asset = (assetParam || '').toUpperCase();

  const [top, setTop] = useState(400);
  const [includeMm, setIncludeMm] = useState(false);
  const [includeHedgers, setIncludeHedgers] = useState(false);
  const [minNotional, setMinNotional] = useState(0);
  const [weighting, setWeighting] = useState<'notional' | 'count' | 'quality'>('notional');
  const [matrix, setMatrix] = useState<MatrixFilters>({});
  const [showFilters, setShowFilters] = useState(true);
  const setM = (patch: MatrixFilters) => setMatrix((m) => ({ ...m, ...patch }));

  const assetsQ = useQuery({ queryKey: ['analytics-assets'], queryFn: getAnalyticsAssets, staleTime: 30_000 });
  const detailQ = useQuery({
    queryKey: ['analytics-asset', asset, top, includeMm, includeHedgers, minNotional, weighting === 'quality', matrix],
    queryFn: () => getAssetDetail(asset, { top, include_mm: includeMm, include_hedgers: includeHedgers, min_notional: minNotional, quality_weights: weighting === 'quality', ...matrix }),
    enabled: !!asset, staleTime: 30_000, refetchInterval: 60_000,
  });
  const smiQ = useQuery({ queryKey: ['analytics-smi', asset], queryFn: () => getSmi(asset), enabled: !!asset, staleTime: 60_000 });
  const flowsQ = useQuery({ queryKey: ['analytics-flows', asset, includeMm], queryFn: () => getFlows({ asset, window: '24h', limit: 30, include_mm: includeMm }), enabled: !!asset, staleTime: 30_000 });

  const d = detailQ.data;
  const pos = d?.positioning;
  const cohort = assetsQ.data?.cohort;
  const strip = useMemo(() => (assetsQ.data?.assets ?? []).slice().sort((a, b) => (b.core?.cohort_oi ?? 0) - (a.core?.cohort_oi ?? 0)).slice(0, 12), [assetsQ.data]);
  const smi = smiQ.data?.latest;
  const v = useMemo(() => (d ? verdict(asset, pos ?? null) : null), [d, pos, asset]);

  const nl = pos?.notional_long ?? 0, ns = pos?.notional_short ?? 0;
  const longShare = nl + ns > 0 ? (nl / (nl + ns)) * 100 : 50;
  const [flowLimit, setFlowLimit] = useState(20);

  if (!d && detailQ.isLoading) return <div className="screen"><div className="empty" style={{ marginTop: 40 }}>Loading {asset}</div></div>;

  const compLabels: [string, keyof NonNullable<typeof smi>['components']][] = [
    ['Positioning skew', 'c1'], ['Flow direction', 'c2'], ['Breadth', 'c3'], ['Leverage appetite', 'c4'], ['Divergence', 'c5'],
  ];
  const age = d?.age_buckets ?? { h24: 0, d1_7: 0, d7_plus: 0, undated: 0 };
  const ageTot = age.h24 + age.d1_7 + age.d7_plus + age.undated || 1;
  const trig = d?.trigger_clusters;

  const tr = smiQ.data?.track_record;

  return (
    <div className="screen">
      {/* 1. title + badges */}
      <div className="head">
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 4 }}>
            <button className="btn sm ghost" style={{ paddingLeft: 0 }} onClick={() => navigate('/analytics')}>← All assets</button>
            <button className="btn sm ghost" onClick={() => navigate(`/analytics/movers?asset=${asset}`)}>Who moved →</button>
          </div>
          <h1>Smart money on {asset}</h1>
          <p>Positions held by the {cohort?.size ?? '—'} largest tracked wallets</p>
          <div className="hbadges">
            <span className="tag accent">{cohort?.size ?? '—'} tracked traders</span>
            <span className="tag flat">{cohort?.mm_flagged ?? 0} market makers flagged</span>
            <span className="tag flat">{cohort?.hedger_flagged ?? 0} hedgers</span>
            <span className="tag flat">{cohort?.mm_unknown ?? 0} unknown</span>
          </div>
        </div>
        <div className="stamp"><i />Full sweep {relAge(d?.computed_at ?? null)}</div>
      </div>

      {/* 2. asset strip */}
      <div className="asset-strip" role="tablist">
        {strip.map((a) => (
          <button key={a.asset} aria-selected={a.asset === asset} onClick={() => navigate(`/analytics/${a.asset}`)}>
            <b>{a.asset}</b><span>{humanUsd(a.core?.cohort_oi ?? 0)} open</span>
          </button>
        ))}
      </div>

      {/* 4. filter card */}
      <div className={`afilters${showFilters ? '' : ' collapsed'}`}>
        <div className="bar">
          <span className="sumline">
            Showing <b>Top {top}</b> wallets, <b>{weighting === 'notional' ? 'dollar weighted' : weighting === 'count' ? 'wallet weighted' : 'quality weighted'}</b>, market makers and hedgers <b>{includeMm && includeHedgers ? 'included' : 'excluded'}</b>
          </span>
          <button className="btn sm" onClick={() => setShowFilters((s) => !s)}>{showFilters ? 'Hide filters' : 'Show filters'}</button>
        </div>
        <div className="panel">
          <div className="fg"><span className="lab">Cohort size<small>wallets ranked by size</small></span>
            <Seg opts={[['Top 10', 10], ['Top 50', 50], ['Top 100', 100], ['Top 400', 400]] as const} value={top} onPick={setTop} /></div>
          <div className="fg"><span className="lab">Include</span>
            <div className="cohort-meta">
              <button className="ck" aria-pressed={includeMm} onClick={() => setIncludeMm((x) => !x)}><i />Market makers ({d?.filters.mm_excluded ?? 0} excluded)</button>
              <button className="ck" aria-pressed={includeHedgers} onClick={() => setIncludeHedgers((x) => !x)}><i />Hedgers ({d?.filters.hedger_excluded ?? 0} excluded)</button>
            </div></div>
          <div className="fg"><span className="lab">Weighting</span>
            <Seg opts={[['By dollars', 'notional'], ['By wallet count', 'count'], ['By quality', 'quality']] as const} value={weighting} onPick={setWeighting} /></div>
          <div className="fg"><span className="lab">Minimum position size</span>
            <Seg opts={[['Any', 0], ['$10K+', 10000], ['$100K+', 100000], ['$1M+', 1000000]] as const} value={minNotional} onPick={setMinNotional} /></div>
          <div className="fg"><span className="lab">Account size</span>
            <Seg opts={ACCT_BANDS} value={matrix.account_band} onPick={(x) => setM({ account_band: x as any })} /></div>
          <div className="fg"><span className="lab">Win rate</span>
            <Seg opts={[['Any', 'any'], ['50%+', 'w50'], ['60%+', 'w60'], ['70%+', 'w70'], ['Consistent', 'cons']] as const}
              value={matrix.consistent_only ? 'cons' : matrix.win_rate_min === 0.7 ? 'w70' : matrix.win_rate_min === 0.6 ? 'w60' : matrix.win_rate_min === 0.5 ? 'w50' : 'any'}
              onPick={(x) => setM({ win_rate_min: x === 'w50' ? 0.5 : x === 'w60' ? 0.6 : x === 'w70' ? 0.7 : undefined, consistent_only: x === 'cons' ? true : undefined })} /></div>
          <div className="fg"><span className="lab">Active in</span>
            <Seg opts={[['Any', undefined], ['Last 7 days', 7], ['Last 30 days', 30]] as const} value={matrix.active_within_days} onPick={(x) => setM({ active_within_days: x as any })} /></div>
          <div className="fg"><span className="lab">Leverage</span>
            <Seg opts={LEV_BANDS} value={matrix.lev_band} onPick={(x) => setM({ lev_band: x as any })} /></div>
          <div className="fg"><span className="lab">Position age</span>
            <Seg opts={AGE_BANDS} value={matrix.age_band} onPick={(x) => setM({ age_band: x as any })} /></div>
          <div className="fg"><span className="lab">Position result</span>
            <Seg opts={[['Any', undefined], ['In profit', 'profit'], ['Underwater', 'underwater']] as const} value={matrix.pnl_state} onPick={(x) => setM({ pnl_state: x as any })} /></div>
        </div>
      </div>

      {d?.low_sample && (
        <div className="tag amber" style={{ marginBottom: 12 }}>
          Small sample: {d.sample_wallets} wallet{d.sample_wallets === 1 ? '' : 's'} in this slice — read with care.
        </div>
      )}

      <div>
        {/* 5. hero */}
        <div className="hero">
          <div className="hero-main">
            <div className="eyebrow">
              <span className={`tag ${v?.stance === 'net_long' ? 'long' : v?.stance === 'net_short' ? 'short' : 'flat'}`}>
                {v?.stance === 'net_long' ? 'Net long' : v?.stance === 'net_short' ? 'Net short' : 'Balanced'}
              </span>
              <span>Top {top} wallets, {weighting === 'notional' ? 'dollar' : weighting === 'count' ? 'wallet' : 'quality'} weighted, sweep {relAge(d?.computed_at ?? null)}</span>
            </div>
            <div className="ratio"><i style={{ width: `${longShare}%` }} /></div>
            <div className="bigline">
              <div className="bignum long-c">{humanUsd(nl)}<small>Long, {pos?.wallets_long ?? 0} wallets</small></div>
              <div />
              <div className="bignum short-c" style={{ textAlign: 'right' }}>{humanUsd(ns)}<small>Short, {pos?.wallets_short ?? 0} wallets</small></div>
            </div>
            <p className="verdict">{v?.headline}{v?.subtext ? ' ' + v.subtext : ''}</p>
          </div>
          <div className="kpis">
            <div className="kpi"><div className="v">{smi ? smi.smi.toFixed(1) : (smiQ.data?.calibrating ? 'calibrating' : '—')}</div><div className="l">Smart Money Index{smiQ.data?.calibrating ? ' · calibrating' : ''}</div></div>
            <div className="kpi"><div className="v">{d?.sample_wallets ?? 0}</div><div className="l">Wallets positioned</div></div>
            <div className="kpi"><div className="v long-c">{(pos?.upnl_long ?? 0) >= 0 ? '+' : '−'}{humanUsd(pos?.upnl_long ?? 0)}</div><div className="l">Unrealized, longs</div></div>
            <div className="kpi"><div className="v short-c">{(pos?.upnl_short ?? 0) >= 0 ? '+' : '−'}{humanUsd(pos?.upnl_short ?? 0)}</div><div className="l">Unrealized, shorts</div></div>
          </div>
        </div>

        {/* 6. entry + trend */}
        <div className="row two" style={{ margin: '16px 0' }}>
          <div className="card entry">
            <h2>Where positions were opened</h2>
            <div className="sub">Long entries above the line in green, short entries below in red.{d?.entry_distribution.mark ? ` Dashed line is the price now, ${humanPx(d.entry_distribution.mark)}.` : ''}</div>
            {d && <EntrySvg long={d.entry_distribution.long} short={d.entry_distribution.short} mark={d.entry_distribution.mark} />}
          </div>
          <div className="card trend48">
            <h2>Positioning over the last 48 hours</h2>
            <div className="sub">Share of cohort wallets on the long side, one point per sweep</div>
            {d && <TrendSvg points={d.trend_48h} />}
          </div>
        </div>

        {/* 7. three cards */}
        <div className="row three">
          <div className="card">
            <h2>Longs against shorts</h2>
            <div className="sub">Each metric mirrored so the imbalance is visible at a glance</div>
            <div className="bf-head"><span>Longs</span><span /><span>Shorts</span></div>
            <Bf label="Wallets" l={pos?.wallets_long ?? 0} r={pos?.wallets_short ?? 0} />
            <Bf label="Net position" l={nl} r={ns} money />
            <Bf label="Average leverage" l={pos?.avg_lev_long ?? null} r={pos?.avg_lev_short ?? null} suffix="x" />
            <Bf label="In profit" l={pos?.longs_in_profit_pct ?? null} r={null} suffix="%" />
            <Bf label="Unrealized" l={pos?.upnl_long ?? 0} r={pos?.upnl_short ?? 0} money signed />
          </div>

          <div className="card">
            <h2>Index breakdown</h2>
            <div className="sub">Five inputs behind the score</div>
            {smi ? (
              <>
                <div className="idx"><span className="v">{smi.smi.toFixed(1)}</span>
                  <span className={`tag ${smi.smi >= 55 ? 'long' : smi.smi <= 45 ? 'short' : 'flat'}`}>{smi.smi >= 55 ? 'Leaning long' : smi.smi <= 45 ? 'Leaning short' : 'Balanced'}</span></div>
                {compLabels.map(([label, key]) => (
                  <div className="comp" key={key}><span>{label}</span><div className="bar"><i style={{ width: `${smi.components[key]}%` }} /></div><span>{smi.components[key].toFixed(0)}</span></div>
                ))}
                {tr && (
                  <p className="dim" style={{ fontSize: 12.5, marginTop: 10 }}>
                    {tr.published && tr.rows?.length
                      ? trackRecordLine(asset, tr.rows[0].kind, tr.rows[0].bucket, tr.rows[0].horizon, tr.rows[0].median_ret ?? 0, tr.rows[0].hit_rate, tr.rows[0].n)
                      : trackRecordCollecting(tr.n_observations, tr.days, tr.publish_min_n, tr.publish_min_days)}
                  </p>
                )}
              </>
            ) : <div className="empty">{smiQ.data?.calibrating ? 'Smart Money Index is calibrating' : 'Not enough data yet'}</div>}
          </div>

          <div className="card">
            <h2>How old is the conviction</h2>
            <div className="sub">{age.h24 > 0 ? `${age.h24} opened today.` : 'Nothing opened today.'} {age.d7_plus >= age.d1_7 ? 'Most positions are older than a week.' : 'Most positioning is recent.'}</div>
            <div className="stack">
              <i style={{ width: `${(age.h24 / ageTot) * 100}%`, background: 'var(--accent)' }} />
              <i style={{ width: `${(age.d1_7 / ageTot) * 100}%`, background: 'var(--long)' }} />
              <i style={{ width: `${(age.d7_plus / ageTot) * 100}%`, background: 'var(--amber)' }} />
              <i style={{ width: `${(age.undated / ageTot) * 100}%`, background: 'var(--s3)' }} />
            </div>
            <div className="legend">
              <div><i style={{ background: 'var(--accent)' }} />Opened today<b>{age.h24}</b></div>
              <div><i style={{ background: 'var(--long)' }} />This week<b>{age.d1_7}</b></div>
              <div><i style={{ background: 'var(--amber)' }} />Older than a week<b>{age.d7_plus}</b></div>
              <div><i style={{ background: 'var(--s3)' }} />Unknown age<b>{age.undated}</b></div>
            </div>
            {trig && (
              <div className="kv" style={{ marginTop: 14 }}>
                <span>Resting take profit or stop loss</span>
                <b>{trig.wallets_with_triggers} of {trig.wallets_checked} checked</b>
              </div>
            )}
          </div>
        </div>

        {/* 8. liq ladder + crowded */}
        <div className="row three" style={{ marginTop: 16 }}>
          <div className="card">
            <h2>Liquidation ladder</h2>
            <div className="sub">Where forced closes stack up above and below the current price</div>
            <LiqLadder below={d?.liq_buckets.below_mark ?? []} above={d?.liq_buckets.above_mark ?? []} mark={d?.venue.mark ?? d?.entry_distribution.mark ?? null} />
            <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>{d ? squeezeTakeaway(d.liq_buckets.below_mark, d.liq_buckets.above_mark) : ''}</p>
          </div>
          <div className="card" style={{ gridColumn: 'span 2' }}>
            <h2>Crowded entries</h2>
            <div className="sub">Same asset and side, entries within ±{d?.crowding?.entry_tol_pct ?? 1}% of each other, {d?.crowding?.min_wallets ?? 3}+ distinct wallets</div>
            {(d?.crowding?.clusters?.length ?? 0) === 0 ? (
              <div className="empty">No crowded trades in this slice</div>
            ) : (
              <table>
                <thead><tr><th>Side</th><th>Wallets</th><th>Entry band</th><th className="r">Combined size</th></tr></thead>
                <tbody>
                  {(d?.crowding?.clusters ?? []).map((c, i) => (
                    <tr key={i}>
                      <td><span className={`tag ${c.side}`}>{c.side === 'long' ? 'Long' : 'Short'}</span></td>
                      <td>{c.wallet_count}{c.entity_count != null && c.entity_count < c.wallet_count ? ` (${c.entity_count})` : ''}</td>
                      <td>{humanPx(c.entry_lo)} to {humanPx(c.entry_hi)}</td>
                      <td className="r amt">{humanUsd(c.notional)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* 9. recent moves */}
        <div className="card" style={{ marginTop: 16 }}>
          <h2>Recent {asset} moves</h2>
          <div className="sub">Every change the cohort made, newest first</div>
          {(flowsQ.data?.events?.length ?? 0) === 0 ? (
            <div className="empty">Flow builds up over the next few sweep cycles</div>
          ) : (
            <table>
              <thead><tr><th>Wallet</th><th>Action</th><th>Detail</th><th className="r">Size change</th><th className="r">When</th></tr></thead>
              <tbody>
                {(flowsQ.data?.events ?? []).slice(0, flowLimit).map((e) => {
                  const f = flowLabel(e);
                  const detail = e.conviction ? `${convPct(e.conviction.pct)} of account as margin` : '';
                  return (
                    <tr key={e.id}>
                      <td className="who mono">{e.display_name || shorten(e.wallet)}</td>
                      <td><span className={`tag ${f.cls}`}>{f.text}</span></td>
                      <td className="muted">{detail}</td>
                      <td className={`r amt ${(e.notional_delta ?? 0) >= 0 ? 'long-c' : 'short-c'}`}>{(e.notional_delta ?? 0) >= 0 ? '+' : '−'}{humanUsd(e.notional_delta ?? 0)}</td>
                      <td className="r dim">{relAge(e.detected_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {(flowsQ.data?.events?.length ?? 0) > flowLimit && (
            <div style={{ marginTop: 12 }}><button className="btn sm" onClick={() => setFlowLimit((n) => n + 20)}>Show 20 more</button></div>
          )}
        </div>
      </div>
    </div>
  );
}

function shorten(w: string): string { return w.length > 12 ? `${w.slice(0, 6)}…${w.slice(-4)}` : w; }
function convPct(pct: number): string { return pct > 0 && pct < 1 ? '<1%' : `${pct.toFixed(0)}%`; }

function Bf({ label, l, r, money, signed, suffix }: { label: string; l: number | null; r: number | null; money?: boolean; signed?: boolean; suffix?: string }) {
  const fmt = (x: number | null) => x == null ? 'n/a' : money ? `${signed && x < 0 ? '−' : signed ? '+' : ''}${humanUsd(x)}` : `${x}${suffix ?? ''}`;
  const mag = (x: number | null) => x == null ? 0 : Math.abs(x);
  const max = Math.max(mag(l), mag(r), 1);
  return (
    <div className="bf">
      <div className="bfs l"><b>{fmt(l)}</b><i className="bar" style={{ width: `${(mag(l) / max) * 100}%` }} /></div>
      <span className="lb">{label}</span>
      <div className="bfs r"><i className="bar" style={{ width: `${(mag(r) / max) * 100}%` }} /><b className={r == null ? 'dim' : ''}>{fmt(r)}</b></div>
    </div>
  );
}

function LiqLadder({ below, above, mark }: { below: EntryBucket[]; above: EntryBucket[]; mark: number | null }) {
  const all = [...above, ...below].filter((b) => b.count > 0);
  if (!all.length) return <div className="empty">No cohort liquidation prices in this slice</div>;
  const max = Math.max(...all.map((b) => b.notional), 1);
  const rowsAbove = [...above].sort((a, b) => b.px_lo - a.px_lo);
  const rowsBelow = [...below].sort((a, b) => b.px_lo - a.px_lo);
  const row = (b: EntryBucket, side: 'short' | 'long', i: number) => (
    <div className="lad" key={side + i}>
      <span className="p">{humanPx(b.px_lo)} to {humanPx(b.px_hi)}</span>
      <div className="bar"><i style={{ width: `${(b.notional / max) * 100}%`, background: `var(--${side})` }} /></div>
      <span className="a">{humanUsd(b.notional)}</span>
    </div>
  );
  return (
    <div className="ladder">
      {rowsAbove.map((b, i) => row(b, 'short', i))}
      <div className="lad now"><span className="p">{mark != null ? `${humanPx(mark)} now` : 'now'}</span><div className="bar" /><span className="a" /></div>
      {rowsBelow.map((b, i) => row(b, 'long', i))}
    </div>
  );
}
