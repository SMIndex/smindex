import { useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getAnalyticsAssets, getAssetDetail, getFlows, getSmi, getAnalyticsContext,
  AssetRollup, FlowEvent, MatrixFilters, CrowdCluster, EntryBucket,
  AccountBand, LevBand, AgeBand, PnlState,
} from '@/lib/analyticsApi';
import {
  verdict, histogramTakeaway, ageTakeaway, venueSentence, humanPx, humanUsd,
  crowdingLine, triggerSparseLine, freshConvictionStat, squeezeTakeaway,
  trackRecordCollecting, trackRecordLine, copyableLine, weightingDivergence,
} from '@/lib/analyticsCopy';
// (liqTakeaway is reached through squeezeTakeaway's fallback)
import {
  PANEL, FreshnessLabel as Freshness, InfoTip, VerdictHeader, SideColumn,
  FlowRow, StatRowList,
} from '@/components/analytics/shared';
import { formatCompact, shortenAddress } from '@/lib/formatters';

// Smart-money Asset Positioning page — verdict-first presentation (owner
// redesign 2026-08-26). Every generated sentence comes from the fixed
// templates in lib/analyticsCopy.ts (unit-tested); the data layer is the
// same /api/analytics payloads, untouched. Freshness labels + provenance
// tooltips on every panel; cohort-vs-venue separation preserved.

// --- entry-price distribution: symmetric mirrored histogram, SVG ----------
function EntryHistogram({ long, short, mark, weighting }: {
  long: { px_lo: number; px_hi: number; count: number; notional: number; quality?: number }[];
  short: { px_lo: number; px_hi: number; count: number; notional: number; quality?: number }[];
  mark: number | null;
  weighting: 'count' | 'notional' | 'quality';
}) {
  const W = 560, H = 190, MID = (H - 26) / 2 + 8, MIN_BAR = 3;
  const all = [...long, ...short];
  if (!all.length) {
    return <div className="text-[11px] py-6 text-center" style={{ color: 'var(--faint)' }}>No positions with entry prices in this slice.</div>;
  }
  const lo = Math.min(...all.map((b) => b.px_lo));
  const hi = Math.max(...all.map((b) => b.px_hi));
  const span = hi - lo || 1;
  const val = (b: { count: number; notional: number; quality?: number }) =>
    weighting === 'count' ? b.count : weighting === 'quality' ? (b.quality ?? 0) : b.notional;
  const maxV = Math.max(...all.map(val), 1);
  const x = (px: number) => ((px - lo) / span) * (W - 20) + 10;
  const barH = (b: { count: number; notional: number }) =>
    val(b) > 0 ? Math.max((val(b) / maxV) * (MID - 18), MIN_BAR) : 0;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 210 }}>
      {long.map((b, i) => (
        <rect key={`l${i}`} x={x(b.px_lo) + 1} width={Math.max(x(b.px_hi) - x(b.px_lo) - 2, 2)}
              y={MID - barH(b)} height={barH(b)} rx={2} fill="var(--green)" opacity={0.8}>
          <title>{`Longs ${b.count} positions · ${formatCompact(b.notional)} · entries ${humanPx(b.px_lo)}–${humanPx(b.px_hi)}`}</title>
        </rect>
      ))}
      {short.map((b, i) => (
        <rect key={`s${i}`} x={x(b.px_lo) + 1} width={Math.max(x(b.px_hi) - x(b.px_lo) - 2, 2)}
              y={MID + 2} height={barH(b)} rx={2} fill="var(--red)" opacity={0.8}>
          <title>{`Shorts ${b.count} positions · ${formatCompact(b.notional)} · entries ${humanPx(b.px_lo)}–${humanPx(b.px_hi)}`}</title>
        </rect>
      ))}
      <line x1={10} x2={W - 10} y1={MID} y2={MID} stroke="var(--border-strong)" strokeWidth={1} />
      {mark != null && mark >= lo && mark <= hi && (
        <g>
          <line x1={x(mark)} x2={x(mark)} y1={8} y2={H - 18} stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="4 3" />
          <text x={x(mark) + 4} y={16} fontSize={9} fill="var(--accent)">now {humanPx(mark)}</text>
        </g>
      )}
      <text x={10} y={H - 6} fontSize={9} fill="var(--faint)">{humanPx(lo)}</text>
      <text x={W - 10} y={H - 6} fontSize={9} fill="var(--faint)" textAnchor="end">{humanPx(hi)}</text>
    </svg>
  );
}

// --- 48h net-positioning trend, tiny SVG line ------------------------------
function TrendSpark({ points }: { points: { t: string; net_notional: number }[] }) {
  const W = 560, H = 70;
  const vals = points.map((p) => p.net_notional);
  const lo = Math.min(...vals, 0), hi = Math.max(...vals, 0);
  const span = hi - lo || 1;
  const px = (i: number) => (i / (points.length - 1)) * (W - 16) + 8;
  const py = (v: number) => H - 12 - ((v - lo) / span) * (H - 24);
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');
  const up = vals[vals.length - 1] >= vals[0];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 80 }}>
      {lo < 0 && hi > 0 && <line x1={8} x2={W - 8} y1={py(0)} y2={py(0)} stroke="var(--border)" strokeWidth={1} strokeDasharray="3 3" />}
      <path d={d} fill="none" stroke={up ? 'var(--green)' : 'var(--red)'} strokeWidth={1.6} />
      {points.map((p, i) => (
        <circle key={i} cx={px(i)} cy={py(p.net_notional)} r={6} fill="transparent">
          <title>{`${new Date(p.t + 'Z').toUTCString().slice(17, 22)} UTC · net ${formatCompact(p.net_notional)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

// --- Phase 3: liquidation cluster map (notional-weighted, above/below mark) --
function LiqMap({ below, above, mark }: {
  below: EntryBucket[]; above: EntryBucket[]; mark: number | null;
}) {
  const all = [...below, ...above];
  if (!all.length) {
    return <div className="text-[11px] py-4 text-center" style={{ color: 'var(--faint)' }}>
      No cohort liquidation prices in this slice.
    </div>;
  }
  const maxN = Math.max(...all.map((b) => b.notional), 1);
  // ramp (guide B3.1): deeper from the now-line = darker; far buckets muted
  const row = (b: EntryBucket, kind: 'short' | 'long', depth: number) => (
    <div key={`${kind}${b.px_lo}`} className="flex items-center gap-2 text-[11px]"
         title={`${b.count} position${b.count === 1 ? '' : 's'} · ${humanUsd(b.notional)} notional would liquidate between ${humanPx(b.px_lo)} and ${humanPx(b.px_hi)}${b.far ? ' (far outliers — grouped)' : ''}`}>
      <span className="rd-mono tabular-nums w-[120px] shrink-0 text-right" style={{ color: 'var(--faint)' }}>
        {b.far
          ? `far (${kind === 'short' ? '>' : '<'}${humanPx(kind === 'short' ? b.px_lo : b.px_hi)})`
          : `${humanPx(b.px_lo)}–${humanPx(b.px_hi)}`}
      </span>
      <div className="flex-1 h-3 rounded-[4px] overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div className="h-full rounded-[4px]"
             style={{ width: `${Math.max((b.notional / maxN) * 100, 3)}%`,
                      background: kind === 'long' ? 'var(--green)' : 'var(--red)',
                      opacity: b.far ? 0.3 : 0.55 + 0.2 * depth }} />
      </div>
      <span className="rd-mono tabular-nums w-[54px] shrink-0" style={{ color: 'var(--dim)' }}>{humanUsd(b.notional)}</span>
      <span className="w-[34px] shrink-0 text-[10px]" style={{ color: 'var(--faint)' }}>{b.count} pos</span>
    </div>
  );
  // mirrored around the now-line: shorts liquidate ABOVE (furthest first so
  // rows nearest the dashed line are nearest the mark), longs BELOW
  const aboveSorted = [...above].sort((a, b) => b.px_lo - a.px_lo);
  const belowSorted = [...below].sort((a, b) => b.px_lo - a.px_lo);
  return (
    <div className="flex flex-col gap-1">
      {aboveSorted.length > 0 && (
        <div className="text-[10px] font-semibold" style={{ color: 'var(--red)' }}>▲ above price — short liquidations</div>
      )}
      {aboveSorted.map((b, i) => row(b, 'short',
        aboveSorted.length > 1 ? 1 - i / (aboveSorted.length - 1) : 0))}
      {mark != null && (
        <div className="flex items-center gap-2 my-0.5">
          <span className="rd-mono text-[10px]" style={{ color: 'var(--accent)' }}>now {humanPx(mark)}</span>
          <div className="flex-1 border-t border-dashed" style={{ borderColor: 'var(--accent)' }} />
        </div>
      )}
      {belowSorted.length > 0 && (
        <div className="text-[10px] font-semibold" style={{ color: 'var(--green)' }}>▼ below price — long liquidations</div>
      )}
      {belowSorted.map((b, i) => row(b, 'long',
        belowSorted.length > 1 ? i / (belowSorted.length - 1) : 0))}
    </div>
  );
}

// --- Phase 3: crowding detector rows (click -> wallet list) ------------------
function CrowdClusterRow({ c }: { c: CrowdCluster }) {
  const mid = (c.entry_lo + c.entry_hi) / 2;
  return (
    <details className="rounded-[8px]" style={{ background: 'var(--surface-2)' }}>
      <summary className="flex items-center gap-2 px-2.5 py-1.5 text-[11.5px] cursor-pointer select-none list-none">
        <span className="rd-mono font-semibold px-1.5 py-0.5 rounded text-[10px]"
              style={{ background: c.side === 'long' ? 'var(--green-soft)' : 'var(--red-soft)',
                       color: c.side === 'long' ? 'var(--green)' : 'var(--red)' }}>
          {c.side.toUpperCase()}
        </span>
        <span style={{ color: 'var(--text)' }} className="font-semibold">{c.wallet_count} wallets</span>
        <span style={{ color: 'var(--dim)' }}>entries {humanPx(c.entry_lo)}–{humanPx(c.entry_hi)}</span>
        <span className="ml-auto rd-mono tabular-nums" style={{ color: 'var(--dim)' }}>{humanUsd(c.notional)}</span>
        <span className="text-[10px]" style={{ color: 'var(--faint)' }}>▾</span>
      </summary>
      <div className="px-2.5 pb-2 flex flex-col gap-1">
        {c.wallets.map((m) => (
          <div key={m.wallet} className="flex items-center gap-2 text-[11px]">
            <span className="rd-mono" style={{ color: 'var(--dim)' }} title={m.wallet}>{shortenAddress(m.wallet)}</span>
            {m.rank != null && <span className="text-[10px]" style={{ color: 'var(--faint)' }}>#{m.rank}</span>}
            <span className="rd-mono tabular-nums" style={{ color: 'var(--faint)' }}>@ {humanPx(m.entry_px)}</span>
            <span className="ml-auto rd-mono tabular-nums" style={{ color: 'var(--dim)' }}>{humanUsd(m.notional)}</span>
            {m.conviction != null && (
              <span className="text-[10px] px-1 rounded"
                    title={`${m.conviction.pct}% of this wallet's account value${m.conviction.flagged ? ' (>25% — high conviction)' : ''}`}
                    style={m.conviction.flagged
                      ? { background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--text)' }
                      : { border: '1px solid var(--border)', color: 'var(--faint)' }}>
                {m.conviction.pct.toFixed(0)}% acct
              </span>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}

const TOPS = [10, 50, 100, 400];
const MIN_NOTIONALS = [0, 10_000, 100_000, 1_000_000];

// --- Phase 3 filter matrix option sets (server enums mirrored) ---------------
const WIN_RATES: { v: number | undefined; label: string }[] = [
  { v: undefined, label: 'any' }, { v: 0.5, label: '50%+' }, { v: 0.6, label: '60%+' }, { v: 0.7, label: '70%+' }];
const ACCOUNT_BANDS: { v: AccountBand | undefined; label: string }[] = [
  { v: undefined, label: 'any' }, { v: 'lt_10k', label: '<$10k' }, { v: '10k_100k', label: '$10k–100k' },
  { v: '100k_1m', label: '$100k–1M' }, { v: '1m_10m', label: '$1M–10M' }, { v: 'gt_10m', label: '$10M+' }];
const LEV_BANDS_UI: { v: LevBand | undefined; label: string }[] = [
  { v: undefined, label: 'any' }, { v: '0_2', label: '0–2x' }, { v: '2_5', label: '2–5x' },
  { v: '5_10', label: '5–10x' }, { v: '10_plus', label: '10x+' }];
const AGE_BANDS_UI: { v: AgeBand | undefined; label: string }[] = [
  { v: undefined, label: 'any' }, { v: 'h24', label: '<24h' }, { v: 'd1_7', label: '1–7d' },
  { v: 'd7_plus', label: '>7d' }, { v: 'undated', label: 'unknown' }];
const PNL_STATES_UI: { v: PnlState | undefined; label: string }[] = [
  { v: undefined, label: 'any' }, { v: 'profit', label: 'in profit' }, { v: 'underwater', label: 'underwater' }];

function ChipGroup<T>({ label, tip, options, value, onPick }: {
  label: string; tip?: string;
  options: { v: T | undefined; label: string }[];
  value: T | undefined;
  onPick: (v: T | undefined) => void;
}) {
  return (
    <span className="flex items-center gap-1.5 flex-wrap" title={tip}>
      <span style={{ color: 'var(--faint)' }}>{label}</span>
      {options.map((o) => (
        <button key={String(o.v)} onClick={() => onPick(o.v)}
                className="px-2 py-0.5 rounded-full border text-[11px]"
                style={value === o.v ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                     : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
          {o.label}
        </button>
      ))}
    </span>
  );
}

export default function AnalyticsPage() {
  const navigate = useNavigate();
  const { assetParam } = useParams<{ assetParam: string }>();
  const [asset, setAsset] = useState<string | null>(assetParam ? assetParam.toUpperCase() : null);
  const [top, setTop] = useState(400);
  const [includeMm, setIncludeMm] = useState(false);
  const [includeHedgers, setIncludeHedgers] = useState(false);
  const [minNotional, setMinNotional] = useState(0);
  const [weighting, setWeighting] = useState<'count' | 'notional' | 'quality'>('notional');
  // Phase-3 filter matrix — one state object, sent verbatim as query params
  const [matrix, setMatrix] = useState<MatrixFilters>({});
  const [sheetOpen, setSheetOpen] = useState(false);
  const [draft, setDraft] = useState<MatrixFilters>({});
  const matrixActive = Object.values(matrix).filter((v) => v !== undefined && v !== false).length;

  const assetsQ = useQuery({
    queryKey: ['analytics-assets'],
    queryFn: getAnalyticsAssets,
    staleTime: 60_000, refetchInterval: 120_000,
  });

  // Perpl-mapped assets first, then the rest by cohort OI (spec 1.6)
  const PERPL_FIRST = ['BTC', 'ETH', 'SOL', 'MON', 'HYPE', 'ZEC', 'PUMP'];
  const assetList: AssetRollup[] = useMemo(() => {
    const rows = assetsQ.data?.assets || [];
    const first = PERPL_FIRST.map((s) => rows.find((r) => r.asset === s)).filter(Boolean) as AssetRollup[];
    const rest = rows.filter((r) => !PERPL_FIRST.includes(r.asset));
    return [...first, ...rest];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assetsQ.data]);

  const selected = asset ?? assetList[0]?.asset ?? null;

  const detailQ = useQuery({
    queryKey: ['analytics-asset', selected, top, includeMm, includeHedgers, minNotional, weighting === 'quality', matrix],
    queryFn: () => getAssetDetail(selected!, {
      top, include_mm: includeMm, include_hedgers: includeHedgers,
      min_notional: minNotional, quality_weights: weighting === 'quality', ...matrix }),
    enabled: !!selected,
    staleTime: 60_000, refetchInterval: 120_000,
  });

  const flowsQ = useQuery({
    queryKey: ['analytics-flows', selected, includeMm],
    queryFn: () => getFlows({ asset: selected!, window: '24h', limit: 30, include_mm: includeMm }),
    enabled: !!selected,
    staleTime: 30_000, refetchInterval: 60_000,
  });

  const smiQ = useQuery({
    queryKey: ['analytics-smi', selected],
    queryFn: () => getSmi(selected!),
    enabled: !!selected,
    staleTime: 60_000, refetchInterval: 120_000,
  });

  // Tier-2 B3: Perpl mapping + holder wallets for the Discover reverse link
  const ctxQ = useQuery({
    queryKey: ['analytics-context', selected],
    queryFn: () => getAnalyticsContext(selected!),
    enabled: !!selected,
    staleTime: 60_000,
  });

  const d = detailQ.data;
  const pos = d?.positioning;
  const cohort = assetsQ.data?.cohort;
  const nl = pos?.notional_long ?? 0;
  const ns = pos?.notional_short ?? 0;
  const qw = d?.quality_weighting;
  const longShare = weighting === 'notional'
    ? (nl + ns > 0 ? (nl / (nl + ns)) * 100 : 50)
    : weighting === 'quality'
      ? (qw && qw.quality_long + qw.quality_short > 0
          ? (qw.quality_long / (qw.quality_long + qw.quality_short)) * 100 : 50)
      : (pos && pos.wallets_long + pos.wallets_short > 0
          ? (pos.wallets_long / (pos.wallets_long + pos.wallets_short)) * 100 : 50);

  // All prose from the tested template module — never assembled inline
  const v = useMemo(() => (selected && d ? verdict(selected, pos ?? null) : null), [selected, d, pos]);
  const histLine = d ? histogramTakeaway(d.entry_distribution.long, d.entry_distribution.short,
                                         d.entry_distribution.mark, d.low_sample) : '';
  const ageLine = d ? ageTakeaway(d.age_buckets) : '';
  const venueLine = d && pos ? venueSentence(d.venue?.oi_usd ?? null, pos.cohort_oi, d.venue?.funding_hourly ?? null) : '';

  // Scope reconciliation (spec pt.5): chips are computed cohort-wide by the
  // API (top-N/MM/min-size filters shape only the detail panels). When any
  // filter is narrowed, the caption states the chips' scope explicitly so two
  // scopes never appear unlabeled. (Filter-respecting chips would need the
  // data layer, which this pass must not touch — documented in the report.)
  const filtersNarrowed = top !== 400 || includeMm || includeHedgers || minNotional > 0 || matrixActive > 0;

  const trendReady = (d?.trend_48h?.length ?? 0) >= 2;
  const flowsReady = (flowsQ.data?.events?.length ?? 0) > 0;
  const coldStart = d && flowsQ.data && !trendReady && !flowsReady;

  return (
    <div className="rd-sans max-w-[1100px] mx-auto pb-10">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <button onClick={() => navigate('/analytics')} className="text-[12px] px-2 py-1 rounded-[7px] hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--dim)', border: '1px solid var(--border)' }}>← Pulse</button>
        <button onClick={() => navigate('/analytics/movers')} className="text-[12px] px-2 py-1 rounded-[7px] hover:bg-[var(--surface-2)]"
                style={{ color: 'var(--dim)', border: '1px solid var(--border)' }}>Who moved →</button>
        <h1 className="text-[20px] font-bold" style={{ color: 'var(--text)' }}>Analytics</h1>
        <span className="text-[11px] px-2 py-0.5 rounded-full"
              title="Every number on this page measures the tracked cohort of top leaderboard traders on Hyperliquid — NOT the whole market. Venue-wide figures are shown separately as context."
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          Top-{cohort?.size ?? '—'} tracked traders
        </span>
        {cohort && (
          <span className="text-[10px]" style={{ color: 'var(--faint)' }}
                title={`${cohort.mm_flagged} wallets flagged as likely market makers (excluded by default), ${cohort.mm_unknown} unknown (included — cannot be proven either way yet)`}>
            {cohort.mm_flagged} MM-flagged{(cohort.hedger_flagged ?? 0) > 0 ? ` · ${cohort.hedger_flagged} hedgers` : ''} · {cohort.mm_unknown} unknown
          </span>
        )}
        <div className="ml-auto">
          <Freshness computedAt={assetsQ.data?.computed_at ?? null} label="full-cohort sweep" />
        </div>
      </div>

      {/* asset selector */}
      <div className="flex gap-1.5 overflow-x-auto rd-scroll pb-1 mb-1">
        {assetList.map((a) => {
          const active = a.asset === selected;
          return (
            <button key={a.asset} onClick={() => { setAsset(a.asset); navigate(`/analytics/${a.asset}`, { replace: true }); }}
                    className="px-2.5 py-1 rounded-[8px] text-[12px] rd-mono font-semibold whitespace-nowrap"
                    style={active ? { background: 'var(--accent)', color: '#fff' }
                                  : { background: 'var(--surface-2)', color: 'var(--dim)', border: '1px solid var(--border)' }}>
              {a.asset}
              <span className="ml-1 text-[9.5px] font-normal" style={{ color: active ? 'rgba(255,255,255,.7)' : 'var(--faint)' }}>
                {formatCompact(a.core?.cohort_oi ?? 0)}
              </span>
            </button>
          );
        })}
        {assetsQ.isLoading && <span className="text-[11px] py-1" style={{ color: 'var(--faint)' }}>loading assets…</span>}
        {!assetsQ.isLoading && assetList.length === 0 && (
          <span className="text-[11px] py-1" style={{ color: 'var(--faint)' }}>
            No sweep data yet — the first cohort cycle populates this page.
          </span>
        )}
      </div>
      <div className="text-[10px] mb-3" style={{ color: 'var(--faint)' }}>
        {filtersNarrowed
          ? `Chip sizes: whole cohort (top-${cohort?.size ?? '—'}, MMs excluded) — panels below use your narrower filter.`
          : 'Chip sizes: cohort open interest per asset (MMs excluded).'}
      </div>

      {/* filters */}
      <div className="flex flex-wrap items-center gap-2 mb-4 text-[11px]">
        <span style={{ color: 'var(--faint)' }}>Cohort</span>
        {TOPS.map((t) => (
          <button key={t} onClick={() => setTop(t)}
                  title={`Restrict to the top ${t} cohort wallets by all-time PnL rank`}
                  className="px-2 py-0.5 rounded-full border"
                  style={top === t ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                   : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
            Top {t}
          </button>
        ))}
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <button onClick={() => setIncludeMm(!includeMm)}
                title={`Market makers (maker ratio ≥ 0.6 with 500+ trades/7d, or gross notional ≥ 25× account value across 8+ assets) distort directional sentiment. ${d?.filters ? `${d.filters.mm_excluded} excluded in this slice; ${d.filters.mm_unknown_included} unknown wallets included.` : ''}`}
                className="px-2 py-0.5 rounded-full border"
                style={includeMm ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                 : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
          {includeMm ? '✓ ' : ''}include MMs{!includeMm && d?.filters ? ` (${d.filters.mm_excluded} excluded)` : ''}
        </button>
        <button onClick={() => setIncludeHedgers(!includeHedgers)}
                title={`Hedgers hold spot ≈ perp-short on the same asset (within 25%, ≥$100k matched leg, from budgeted spot-state sampling) — their shorts are inventory hedges, not directional bets. Unknown wallets stay included. ${d?.filters ? `${d.filters.hedger_excluded ?? 0} excluded in this slice.` : ''}`}
                className="px-2 py-0.5 rounded-full border"
                style={includeHedgers ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                      : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
          {includeHedgers ? '✓ ' : ''}include hedgers{!includeHedgers && d?.filters && (d.filters.hedger_excluded ?? 0) > 0 ? ` (${d.filters.hedger_excluded} excluded)` : ''}
        </button>
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <span style={{ color: 'var(--faint)' }}>Min size</span>
        {MIN_NOTIONALS.map((m) => (
          <button key={m} onClick={() => setMinNotional(m)}
                  className="px-2 py-0.5 rounded-full border"
                  style={minNotional === m ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                           : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
            {m === 0 ? 'any' : `${formatCompact(m)}+`}
          </button>
        ))}
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <div className="rd-seg flex rounded-[8px] p-0.5" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          {(['notional', 'count', 'quality'] as const).map((w) => (
            <button key={w} onClick={() => setWeighting(w)}
                    title={w === 'notional' ? 'Weight by position dollar size — one whale ≠ one vote'
                      : w === 'count' ? 'One wallet = one vote'
                      : 'Each wallet weighted by win-rate × profit-factor (capped ×3) × consistency, normalized. Wallets without stats weigh 0 — the panel says how many.'}
                    className="px-2 py-0.5 rounded-[6px]"
                    style={weighting === w ? { background: 'var(--accent)', color: '#fff' } : { color: 'var(--dim)' }}>
              {w === 'notional' ? '$-weighted' : w === 'count' ? 'count' : 'quality'}
            </button>
          ))}
        </div>
        {/* mobile: matrix filters live in a bottom sheet (filter-redesign pattern) */}
        <button onClick={() => { setDraft(matrix); setSheetOpen(true); }}
                className="md:hidden px-2 py-0.5 rounded-full border"
                style={matrixActive > 0 ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', borderColor: 'transparent' }
                                        : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
          More filters{matrixActive > 0 ? ` (${matrixActive})` : ''}
        </button>
      </div>

      {/* Phase-3 filter matrix — desktop inline row; every filter server-side */}
      <div className="hidden md:flex flex-wrap items-center gap-x-3 gap-y-2 mb-4 text-[11px]">
        <ChipGroup label="Win rate" tip="7d win-rate floor from fill-stats sampling; wallets without a measured win rate are excluded while this is set."
                   options={WIN_RATES} value={matrix.win_rate_min}
                   onPick={(v) => setMatrix({ ...matrix, win_rate_min: v })} />
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <button onClick={() => setMatrix({ ...matrix, consistent_only: matrix.consistent_only ? undefined : true })}
                title="Profitable in at least 3 of the 4 leaderboard windows (day/week/month/all-time); wallets without metrics are excluded while this is on."
                className="px-2 py-0.5 rounded-full border"
                style={matrix.consistent_only ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                              : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
          {matrix.consistent_only ? '✓ ' : ''}consistent
        </button>
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <ChipGroup label="Account" tip="Wallet account value band (from the sweep's persisted wallet state); wallets with unknown account value are excluded while this is set."
                   options={ACCOUNT_BANDS} value={matrix.account_band}
                   onPick={(v) => setMatrix({ ...matrix, account_band: v })} />
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <ChipGroup label="Active" tip="At least one active trading day within the window (activity metrics)."
                   options={[{ v: undefined, label: 'any' }, { v: 7 as const, label: '7d' }, { v: 30 as const, label: '30d' }]}
                   value={matrix.active_within_days}
                   onPick={(v) => setMatrix({ ...matrix, active_within_days: v })} />
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <ChipGroup label="Leverage" tip="Position leverage band; tracker-sourced rows carry no leverage and are excluded while this is set."
                   options={LEV_BANDS_UI} value={matrix.lev_band}
                   onPick={(v) => setMatrix({ ...matrix, lev_band: v })} />
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <ChipGroup label="Age" tip="Position age band from the funding-ledger dating cache ('unknown' = never dated)."
                   options={AGE_BANDS_UI} value={matrix.age_band}
                   onPick={(v) => setMatrix({ ...matrix, age_band: v })} />
        <span className="w-px h-4" style={{ background: 'var(--border)' }} />
        <ChipGroup label="PnL" tip="In profit / underwater by unrealized PnL (or entry vs current mark when uPnL is unknown)."
                   options={PNL_STATES_UI} value={matrix.pnl_state}
                   onPick={(v) => setMatrix({ ...matrix, pnl_state: v })} />
        {matrixActive > 0 && (
          <button onClick={() => setMatrix({})} className="px-2 py-0.5 rounded-full"
                  style={{ color: 'var(--accent-2)' }}>
            reset ({matrixActive})
          </button>
        )}
      </div>

      {/* VERDICT HEADER (shared component — Design Guide Section A) */}
      {selected && d && v && (
        <VerdictHeader stance={v.stance} lowSample={v.lowSample}
                       headline={v.headline} subtext={v.subtext}
                       right={<Freshness computedAt={d.computed_at} label="full-cohort sweep" />} />
      )}

      {selected && d && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* positioning block — split side columns */}
          <div className="rounded-[14px] p-4" style={PANEL}>
            <div className="flex items-center gap-2 mb-3">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>{selected} positioning</h2>
              <InfoTip text="Cohort wallets' live positions from the 20-minute clearinghouseState sweep." />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label="full-cohort sweep" /></div>
            </div>
            {pos && (
              <>
                <div className="h-2.5 rounded-full overflow-hidden flex mb-3" style={{ background: 'var(--surface-2)' }}
                     title={weighting === 'notional' ? 'Share of cohort dollars long vs short'
                       : weighting === 'quality' ? 'Quality-weighted long vs short share (win-rate × consistency, normalized)'
                       : 'Share of cohort wallets long vs short'}>
                  <div style={{ width: `${longShare}%`, background: 'var(--green)' }} />
                  <div style={{ width: `${100 - longShare}%`, background: 'var(--red)' }} />
                </div>
                <div className="flex flex-col sm:flex-row gap-2.5 mb-1">
                  <SideColumn side="long" rows={[
                    { label: 'Wallets', value: pos.entities_long != null && pos.entities_long < pos.wallets_long
                        ? `${pos.wallets_long} (${pos.entities_long} entities)` : String(pos.wallets_long),
                      tip: pos.entities_long != null && pos.entities_long < pos.wallets_long
                        ? `${pos.wallets_long} wallets deduped as ${pos.entities_long} entities (same-entity fill-timing clusters — one vote per cluster in count views).` : undefined },
                    { label: 'Notional', value: formatCompact(pos.notional_long) },
                    { label: 'Avg leverage', value: pos.avg_lev_long != null ? `${pos.avg_lev_long}x` : '—',
                      tip: 'Positions sourced from the real-time tracker carry no leverage field and are excluded (never invented).' },
                    { label: 'Unrealized PnL', value: formatCompact(pos.upnl_long) },
                    { label: 'In profit', value: pos.longs_in_profit_pct != null ? `${pos.longs_in_profit_pct}%` : '—',
                      tip: 'Share of longs whose entry is below the current mark.' },
                  ]} />
                  <SideColumn side="short" rows={[
                    { label: 'Wallets', value: pos.entities_short != null && pos.entities_short < pos.wallets_short
                        ? `${pos.wallets_short} (${pos.entities_short} entities)` : String(pos.wallets_short),
                      tip: pos.entities_short != null && pos.entities_short < pos.wallets_short
                        ? `${pos.wallets_short} wallets deduped as ${pos.entities_short} entities (same-entity fill-timing clusters — one vote per cluster in count views).` : undefined },
                    { label: 'Notional', value: formatCompact(pos.notional_short) },
                    { label: 'Avg leverage', value: pos.avg_lev_short != null ? `${pos.avg_lev_short}x` : '—',
                      tip: 'Positions sourced from the real-time tracker carry no leverage field and are excluded (never invented).' },
                    { label: 'Unrealized PnL', value: formatCompact(pos.upnl_short) },
                    { label: 'Net (whole slice)', value: `${pos.net_notional >= 0 ? '+' : '−'}${formatCompact(Math.abs(pos.net_notional))}`,
                      tip: 'Cohort long notional minus short notional (whole slice, not just shorts).' },
                  ]} />
                </div>
              </>
            )}
            {/* venue context — one sentence, visually separated */}
            <div className="mt-2 pt-3 text-[11px]" style={{ borderTop: '1px dashed var(--border-strong)', color: 'var(--dim)' }}
                 title="Venue-wide context from Hyperliquid metaAndAssetCtxs — the WHOLE market, not the cohort.">
              <span className="font-semibold" style={{ color: 'var(--faint)' }}>VENUE · </span>
              {venueLine || 'Venue context unavailable this cycle.'}
              {d.venue?.mark != null && <span style={{ color: 'var(--faint)' }}> · mark {humanPx(d.venue.mark)}</span>}
            </div>
            {/* Tier-2 B3: Discover reverse link — Perpl-mapped assets only */}
            {ctxQ.data?.perpl && (ctxQ.data.holders_total ?? 0) > 0 && (
              <button type="button"
                onClick={() => navigate(`/copy/discover?wallets=${(ctxQ.data!.holders ?? []).join(',')}&asset=${selected}`)}
                className="mt-2 text-[11px] font-medium hover:underline"
                style={{ color: 'var(--accent)' }}
                title={`Opens Discover filtered to the tracked cohort wallets currently holding ${selected} (top ${(ctxQ.data.holders ?? []).length} by notional, of ${ctxQ.data.holders_total}).`}>
                {copyableLine(ctxQ.data.holders_total!)} →
              </button>
            )}
          </div>

          {/* entry distribution */}
          <div className="rounded-[14px] p-4" style={PANEL}>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Entry distribution</h2>
              <InfoTip text="Cohort entry prices bucketed per side (longs above the axis, shorts below). Sweep data." />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label="full-cohort sweep" /></div>
            </div>
            <div className="text-[10px] mb-1" style={{ color: 'var(--faint)' }}>
              Green = longs, pink = shorts · dashed = price now
            </div>
            <EntryHistogram long={d.entry_distribution.long} short={d.entry_distribution.short}
                            mark={d.entry_distribution.mark} weighting={weighting} />
            {weighting === 'quality' && d.quality_weighting && (
              <div className="text-[11px] mt-1" style={{ color: 'var(--dim)' }}
                   title="Quality weight = win-rate × profit-factor (capped ×3) × consistency flag, normalized over this slice.">
                Quality weighting (win-rate × PF × consistency) — {d.quality_weighting.weighted_wallets} wallets weighted,
                {' '}{d.quality_weighting.excluded_wallets} with no usable stats weigh 0.
                {pos && d.quality_weighting.weighted_wallets > 0 && (() => {
                  const dollar = nl + ns > 0 ? (nl / (nl + ns)) * 100 : 50;
                  const cnt = pos.wallets_long + pos.wallets_short > 0
                    ? (pos.wallets_long / (pos.wallets_long + pos.wallets_short)) * 100 : 50;
                  const q = d.quality_weighting!.quality_long + d.quality_weighting!.quality_short > 0
                    ? (d.quality_weighting!.quality_long / (d.quality_weighting!.quality_long + d.quality_weighting!.quality_short)) * 100 : null;
                  const line = weightingDivergence(dollar, cnt, q);
                  return line ? <div className="mt-0.5">{line}</div> : null;
                })()}
              </div>
            )}
            {histLine && (
              <div className="text-[11.5px] mt-1" style={{ color: 'var(--dim)' }}>{histLine}</div>
            )}
          </div>

          {/* age panel */}
          <div className="rounded-[14px] p-4" style={PANEL}>
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Position age</h2>
              <InfoTip text="Ages from the funding-ledger dating cache; positions never dated show honestly as Unknown age." />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label="dating cache" /></div>
            </div>
            <StatRowList rows={[
              { label: 'New today', value: d.age_buckets.h24, highlight: d.age_buckets.h24 > 0 },
              { label: 'This week', value: d.age_buckets.d1_7 },
              { label: 'Older than a week', value: d.age_buckets.d7_plus },
              { label: 'Unknown age', value: d.age_buckets.undated },
            ]} />
            {ageLine && <div className="text-[11.5px] mt-2" style={{ color: 'var(--dim)' }}>{ageLine}</div>}
            {freshConvictionStat(d.age_buckets) && (
              <div className="text-[11.5px] mt-1" style={{ color: 'var(--dim)' }}
                   title="Share of positions with a KNOWN open date that opened in the last 24h — unknown-age positions are not counted either way.">
                {freshConvictionStat(d.age_buckets)}
              </div>
            )}
          </div>

          {/* Smart Money Index — sub-scores + raw inputs on demand (no black boxes) */}
          {smiQ.data?.latest && (
            <div className="rounded-[14px] p-4" style={PANEL}>
              <div className="flex items-center gap-2 mb-2">
                <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Smart Money Index</h2>
                <InfoTip text="0-100 composite of the CORE cohort's positioning: skew 30%, 24h flow 25%, breadth 15%, leverage appetite 15%, funding divergence 15%. Formulas in services/analytics/smi.py; every raw input below." />
                {smiQ.data.calibrating && (
                  <span className="text-[9.5px] px-1.5 py-0.5 rounded"
                        title={`Trailing components need 48h of history; ${smiQ.data.history_hours}h collected.`}
                        style={{ background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--dim)' }}>
                    calibrating
                  </span>
                )}
                <div className="ml-auto"><Freshness computedAt={smiQ.data.latest.cycle_ts} label="per sweep cycle" /></div>
              </div>
              <div className="flex items-center gap-3 mb-2">
                <span className="rd-mono text-[30px] font-extrabold tabular-nums"
                      style={{ color: smiQ.data.latest.smi >= 52 ? 'var(--green)' : smiQ.data.latest.smi <= 48 ? 'var(--red)' : 'var(--text)' }}>
                  {smiQ.data.latest.smi.toFixed(1)}
                </span>
                {smiQ.data.series.length >= 2 && (
                  <svg viewBox="0 0 200 40" className="flex-1" style={{ maxHeight: 44 }}>
                    {(() => {
                      const ser = smiQ.data!.series;
                      const lo = Math.min(...ser.map((p) => p.smi), 45);
                      const hi = Math.max(...ser.map((p) => p.smi), 55);
                      const span = hi - lo || 1;
                      const path = ser.map((p, i) =>
                        `${i ? 'L' : 'M'}${(i / (ser.length - 1)) * 194 + 3},${37 - ((p.smi - lo) / span) * 34}`).join(' ');
                      const mid = 37 - ((50 - lo) / span) * 34;
                      return (<>
                        {lo < 50 && hi > 50 && <line x1={3} x2={197} y1={mid} y2={mid} stroke="var(--border)" strokeWidth={0.75} strokeDasharray="3 3" />}
                        <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.4} />
                      </>);
                    })()}
                  </svg>
                )}
              </div>
              <div className="flex flex-col gap-1">
                {([['Positioning skew', 'c1', 'notional net-long share ×100'],
                   ['Flow direction', 'c2', '24h net flow / cohort OI, ±25% pins'],
                   ['Breadth', 'c3', '100 − |count skew − notional skew|×100'],
                   ['Leverage appetite', 'c4', '50 × current lev / 30d median'],
                   ['Divergence', 'c5', '50 + 50×(−skew × funding/1bp)'],
                  ] as const).map(([label, key, formula]) => {
                  const val = smiQ.data!.latest!.components[key];
                  return (
                    <div key={key} className="flex items-center gap-2 text-[11px]" title={formula}>
                      <span className="w-[110px] shrink-0" style={{ color: 'var(--faint)' }}>{label}</span>
                      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                        <div className="h-full rounded-full" style={{ width: `${val}%`,
                             background: val >= 52 ? 'var(--green)' : val <= 48 ? 'var(--red)' : 'var(--dim)' }} />
                      </div>
                      <span className="rd-mono tabular-nums w-9 text-right" style={{ color: 'var(--text)' }}>{val.toFixed(0)}</span>
                    </div>
                  );
                })}
              </div>
              <details className="mt-2">
                <summary className="text-[10.5px] cursor-pointer" style={{ color: 'var(--faint)' }}>raw inputs (audit)</summary>
                <pre className="text-[9.5px] mt-1 p-2 rounded-[8px] overflow-x-auto rd-scroll"
                     style={{ background: 'var(--surface-2)', color: 'var(--dim)' }}>
                  {JSON.stringify(smiQ.data.latest.inputs, null, 1)}
                </pre>
              </details>
              {/* Track record (Tier-2 Part A4): the SERVER gates publication —
                  rows exist in the payload only past n>=30 & span>=21d. Below
                  it we can only narrate the collecting counters it returns. */}
              {smiQ.data.track_record && (
                <div className="mt-3 pt-2" style={{ borderTop: '1px solid var(--border)' }}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[11px] font-bold" style={{ color: 'var(--text)' }}>Track record</span>
                    <InfoTip text="Forward price change measured 4h/24h/72h after each SMI observation (nightly study; services/analytics/smi_study.py). Nothing publishes below 30 observations per bucket AND 21 days of history — enforced server-side, no override." />
                  </div>
                  {smiQ.data.track_record.published && smiQ.data.track_record.rows ? (
                    <div className="flex flex-col gap-0.5">
                      {smiQ.data.track_record.rows
                        .filter((r) => r.median_ret != null)
                        .sort((a, b) => (b.n - a.n))
                        .slice(0, 6)
                        .map((r) => (
                          <div key={`${r.kind}-${r.bucket}-${r.horizon}`} className="text-[11px]" style={{ color: 'var(--dim)' }}>
                            {trackRecordLine(selected!, r.kind, r.bucket, r.horizon, r.median_ret!, r.hit_rate, r.n)}
                          </div>
                        ))}
                    </div>
                  ) : (
                    <div className="text-[11px]" style={{ color: 'var(--faint)' }}>
                      {trackRecordCollecting(smiQ.data.track_record.n_observations,
                        smiQ.data.track_record.days,
                        smiQ.data.track_record.publish_min_n,
                        smiQ.data.track_record.publish_min_days)}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* trend — only when it has data; cold start collapses into one notice */}
          {trendReady && (
            <div className="rounded-[14px] p-4" style={PANEL}>
              <div className="flex items-center gap-2 mb-1">
                <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>48h positioning trend</h2>
                <InfoTip text="Net cohort notional (long − short) per 20-minute sweep cycle, core cohort (MMs excluded)." />
                <div className="ml-auto"><Freshness computedAt={d.computed_at} label="full-cohort sweep" /></div>
              </div>
              <TrendSpark points={d.trend_48h} />
            </div>
          )}

          {/* B3 depth row: liq clusters (wide) + right column crowding/TPSL */}
          <div className="lg:col-span-2 grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="rounded-[14px] p-4 lg:col-span-2" style={PANEL}>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Liquidation clusters</h2>
              <InfoTip text="Cohort liquidation prices bucketed above/below the current mark, notional-weighted. Liq prices come straight from clearinghouseState — never computed by us." />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label="full-cohort sweep" /></div>
            </div>
            <div className="text-[10px] mb-2 px-2 py-1 rounded-[6px]"
                 style={{ background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--dim)' }}>
              Estimates — cross-margin liquidation prices shift with every account change.
            </div>
            <LiqMap below={d.liq_buckets.below_mark} above={d.liq_buckets.above_mark}
                    mark={d.venue?.mark ?? d.entry_distribution.mark} />
            <div className="text-[11.5px] mt-2" style={{ color: 'var(--dim)' }}>
              {squeezeTakeaway(d.liq_buckets.below_mark, d.liq_buckets.above_mark)}
            </div>
          </div>

          <div className="flex flex-col gap-4">
          {/* B3.2 crowded trade — exceptional by nature: rendered ONLY when a crowd exists */}
          {(d.crowding?.clusters?.length ?? 0) > 0 && (
          <div className="rounded-[14px] p-4" style={{ background: 'var(--surface)', border: '1px solid var(--accent-soft)' }}>
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Crowded trades</h2>
              <InfoTip text={`Same asset + same side + entry prices within ±${d.crowding?.entry_tol_pct ?? 1}% of each other, ${d.crowding?.min_wallets ?? 3}+ distinct wallets. Computed on the current filter slice. Click a row for the wallet list.`} />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label="full-cohort sweep" /></div>
            </div>
            <div className="flex flex-col gap-1.5">
              {(d.crowding?.clusters ?? []).map((c, i) => <CrowdClusterRow key={i} c={c} />)}
            </div>
            <div className="text-[11.5px] mt-2" style={{ color: 'var(--dim)' }}>
              {crowdingLine((d.crowding?.clusters ?? [])[0] ?? null,
                            d.crowding?.min_wallets ?? 3, d.crowding?.entry_tol_pct ?? 1)}
            </div>
          </div>
          )}

          {/* B3.3 on-chain TP/SL (honest sparse default) */}
          <div className="rounded-[14px] p-4" style={PANEL}>
            <div className="flex items-center gap-2 mb-2">
              <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Resting TP/SL</h2>
              <InfoTip text={`Resting trigger orders observed when cohort profiles are freshly loaded (no extra venue calls, so coverage is partial by design). Clusters are only claimed at ${d.trigger_clusters?.min_claim ?? 5}+ observed triggers.`} />
              <div className="ml-auto"><Freshness computedAt={d.computed_at} label={`${d.trigger_clusters?.window_days ?? 7}d observation window`} /></div>
            </div>
            {d.trigger_clusters?.claimed ? (
              <>
                <div className="text-[11px] mb-1" style={{ color: 'var(--dim)' }}>
                  {d.trigger_clusters.observed} resting triggers observed across checked wallets.
                </div>
                {(['above_mark', 'below_mark'] as const).map((k) =>
                  d.trigger_clusters.buckets[k].map((b, i) => (
                    <div key={`${k}${i}`} className="flex items-center gap-2 text-[11px] mb-0.5">
                      <span className="rd-mono tabular-nums w-[120px] shrink-0 text-right" style={{ color: 'var(--faint)' }}>
                        {humanPx(b.px_lo)}–{humanPx(b.px_hi)}
                      </span>
                      <span style={{ color: 'var(--dim)' }}>{b.count} trigger{b.count === 1 ? '' : 's'} {k === 'above_mark' ? 'above' : 'below'} price</span>
                    </div>
                  )))}
              </>
            ) : (
              <div className="text-[11.5px]" style={{ color: 'var(--dim)' }}>
                {triggerSparseLine(d.trigger_clusters?.wallets_with_triggers ?? 0,
                                   d.trigger_clusters?.wallets_checked ?? 0)}
              </div>
            )}
          </div>
          </div>
          </div>

          {/* flow tape — only when it has data */}
          {flowsReady && (
            <div className="rounded-[14px] p-4 lg:col-span-2" style={PANEL}>
              <div className="flex items-center gap-2 mb-2">
                <h2 className="text-[13px] font-bold" style={{ color: 'var(--text)' }}>Recent flow — {selected}</h2>
                <InfoTip text="Position changes detected between sweep cycles (20m resolution) unified with real-time tracker fills (ws resolution). '~' marks approximate reference prices." />
                <div className="ml-auto"><Freshness computedAt={flowsQ.data?.computed_at ?? null} label="24h window · mixed resolution" /></div>
              </div>
              <div className="flex flex-col gap-1">
                {(flowsQ.data?.events || []).map((e) => <FlowRow key={e.id} e={e} />)}
              </div>
            </div>
          )}

          {/* combined cold-start notice (spec pt.7): one quiet line, not two empty panels */}
          {coldStart && (
            <div className="rounded-[14px] p-4 lg:col-span-2 text-center text-[11.5px]" style={{ ...PANEL, color: 'var(--faint)' }}>
              Flow and trend build up over the next few sweep cycles — first data ~40 min.
            </div>
          )}
          {!coldStart && !trendReady && flowsReady && (
            <div className="rounded-[14px] p-4 text-center text-[11.5px]" style={{ ...PANEL, color: 'var(--faint)' }}>
              Trend appears after a few sweep cycles.
            </div>
          )}
          {!coldStart && trendReady && !flowsReady && (
            <div className="rounded-[14px] p-4 lg:col-span-2 text-center text-[11.5px]" style={{ ...PANEL, color: 'var(--faint)' }}>
              No flow events in the last 24h for this slice.
            </div>
          )}
        </div>
      )}

      {selected && detailQ.isLoading && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[0, 1].map((i) => (
            <div key={i} className="rounded-[14px] h-64 animate-pulse" style={{ background: 'var(--surface-2)' }} />
          ))}
        </div>
      )}

      {/* mobile filter-matrix bottom sheet (filter-redesign pattern: portal,
          translateY transition, DRAFT state committed on Apply) */}
      {sheetOpen && createPortal(
        <div className="fixed inset-0 z-[80]" role="dialog" aria-label="Analytics filters">
          <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,.45)' }}
               onClick={() => setSheetOpen(false)} />
          <div className="absolute left-0 right-0 bottom-0 rounded-t-[16px] p-4 overflow-y-auto rd-scroll rd-sans"
               style={{ background: 'var(--surface)', borderTop: '1px solid var(--border)',
                        maxHeight: '82dvh', paddingBottom: 'calc(env(safe-area-inset-bottom) + 12px)',
                        animation: 'none' }}>
            <div className="mx-auto w-10 h-1 rounded-full mb-3" style={{ background: 'var(--border-strong)' }} />
            <div className="flex items-center mb-3">
              <span className="text-[14px] font-bold" style={{ color: 'var(--text)' }}>Filters</span>
              <button className="ml-auto text-[12px]" style={{ color: 'var(--accent-2)' }}
                      onClick={() => setDraft({})}>Reset</button>
            </div>
            <div className="flex flex-col gap-3 text-[12px]">
              <ChipGroup label="Win rate" options={WIN_RATES} value={draft.win_rate_min}
                         onPick={(v) => setDraft({ ...draft, win_rate_min: v })} />
              <button onClick={() => setDraft({ ...draft, consistent_only: draft.consistent_only ? undefined : true })}
                      className="self-start px-2 py-0.5 rounded-full border text-[11px]"
                      style={draft.consistent_only ? { background: 'var(--accent)', color: '#fff', borderColor: 'transparent' }
                                                   : { borderColor: 'var(--border)', color: 'var(--dim)' }}>
                {draft.consistent_only ? '✓ ' : ''}consistent (3 of 4 windows profitable)
              </button>
              <ChipGroup label="Account" options={ACCOUNT_BANDS} value={draft.account_band}
                         onPick={(v) => setDraft({ ...draft, account_band: v })} />
              <ChipGroup label="Active" value={draft.active_within_days}
                         options={[{ v: undefined, label: 'any' }, { v: 7 as const, label: '7d' }, { v: 30 as const, label: '30d' }]}
                         onPick={(v) => setDraft({ ...draft, active_within_days: v })} />
              <ChipGroup label="Leverage" options={LEV_BANDS_UI} value={draft.lev_band}
                         onPick={(v) => setDraft({ ...draft, lev_band: v })} />
              <ChipGroup label="Age" options={AGE_BANDS_UI} value={draft.age_band}
                         onPick={(v) => setDraft({ ...draft, age_band: v })} />
              <ChipGroup label="PnL" options={PNL_STATES_UI} value={draft.pnl_state}
                         onPick={(v) => setDraft({ ...draft, pnl_state: v })} />
            </div>
            <button className="mt-4 w-full py-2.5 rounded-[10px] text-[13px] font-bold"
                    style={{ background: 'var(--accent)', color: '#fff' }}
                    onClick={() => { setMatrix(draft); setSheetOpen(false); }}>
              Apply
            </button>
          </div>
        </div>,
        document.body)}
    </div>
  );
}
