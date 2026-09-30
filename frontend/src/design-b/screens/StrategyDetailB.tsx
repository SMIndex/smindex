import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import TradingChart from '@/components/terminal/TradingChart';
import { getStrategyDetail, getChangelog, getBreakdown, patchMode, patchParameter, runBacktest, isModelNum } from '@/lib/strategiesApi';
import { ago, useNow } from './StrategiesOverviewB';
import type { SignalCondition, Parameter, Signal, Trade, MindReason } from '@/types/strategies';

// Design B — Strategy detail. Everything shown comes from the latest evaluation
// rows the worker writes (per asset): live condition values, warming labels,
// score vs fire threshold, and a live evaluation log (last 50, 10s refresh).
// Paper only — Live is disabled: no execution adapter built. Nothing trades.
//
// Model rows (M1–M6, docs 10–17 step 5) render a Mind panel instead of the
// score/conditions card: one row per Mind reason (strength bar, weight,
// contribution), the vetoes row, the multipliers row, conviction + size tier
// and the thesis; the Breakdown tab shows the calibration table + weight
// history from /{sid}/breakdown. Strategy 01–06 pages are unchanged.

const MARKET_ID: Record<string, number> = { BTC: 1, ETH: 20 };
const WINDOWS = ['7d', '30d', '90d', 'all'] as const;
const usd = (n: number) => `${n < 0 ? '-' : ''}$${Math.abs(n).toFixed(2)}`;

function condIcon(c: SignalCondition) {
  if (c.met === true) return <span className="ci ok">✓</span>;
  if (c.met === false) return <span className="ci no">✕</span>;
  return <span className="ci wait" title={c.warming ?? 'not evaluated'} />;
}

export default function StrategyDetailB() {
  const { id = '05' } = useParams();
  const isModel = isModelNum(id);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const now = useNow();
  const [asset, setAsset] = useState<'BTC' | 'ETH'>('BTC');
  const [win, setWin] = useState<(typeof WINDOWS)[number]>('30d');
  const [tab, setTab] = useState<'log' | 'trades' | 'breakdown' | 'parameters' | 'changelog'>('log');
  const [drawer, setDrawer] = useState(false);
  const [btReport, setBtReport] = useState<string | null>(null);
  const [btMeta, setBtMeta] = useState<string>('');
  const btMut = useMutation({
    mutationFn: (refresh: boolean) => runBacktest(id, refresh),
    onSuccess: (r) => { setBtReport(r.report); setBtMeta(r.cached ? `cached report generated ${r.generated_ts ? ago(r.generated_ts * 1000, Date.now()) : ''}` : 'replayed just now'); },
    onError: () => setBtReport('Backtest failed — the strategies API is unavailable. Try again shortly.'),
  });

  const detail = useQuery({ queryKey: ['strategy-detail', id], queryFn: () => getStrategyDetail(id), refetchInterval: 10_000 });
  const changelog = useQuery({ queryKey: ['strategy-changelog', id], queryFn: () => getChangelog(id), enabled: tab === 'changelog' });
  const breakdown = useQuery({ queryKey: ['strategy-breakdown', id], queryFn: () => getBreakdown(id), enabled: isModel && tab === 'breakdown', refetchInterval: 60_000 });

  const modeMut = useMutation({
    mutationFn: (m: string) => patchMode(id, m),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategy-detail', id] }),
  });

  const d = detail.data;
  const s = d?.state;
  const latest: Signal | undefined = d?.latest?.[asset];
  const conditions: SignalCondition[] = latest?.conditions?.length ? latest.conditions : (d?.conditions ?? []);
  const stats = d?.stats?.[win];
  const warmLabels = latest?.warming ?? s?.warming ?? [];
  const entry = latest?.intents?.[0];

  return (
    <div className="screen">
      <div className="crumb"><button className="btn sm ghost" style={{ paddingLeft: 0 }} onClick={() => navigate('/strategies')}>Strategies</button> / {id} {s?.name ?? ''}</div>

      <div className="head">
        <div>
          <h1>{s?.name ?? `Strategy ${id}`}
            <span className={`tag ${s?.effective_mode === 'paper' ? 'amber' : 'flat'}`} style={{ marginLeft: 8 }}>{s?.effective_mode === 'paper' ? 'Paper' : s?.effective_mode === 'live' ? 'Live' : 'Evaluating only'}</span>
            {id === '06' && <span className="tag accent" style={{ marginLeft: 6 }}>gated</span>}
            {id === '06u' && <span className="tag accent" style={{ marginLeft: 6 }}>ungated comparison</span>}
            {id === '05c' && <span className="tag accent" style={{ marginLeft: 6 }}>chase-entry comparison</span>}
            {isModel && <span className="tag accent" style={{ marginLeft: 6 }} title="heuristic-mind model — deterministic reasons, vetoes and conviction; no score">Model {id}</span>}
          </h1>
          <p>{isModel
            ? <>15-minute BTC and ETH on Hyperliquid · heuristic Mind: any veto skips, conviction below 0.55 skips, 0.55–0.70 half size, 0.70+ full · last evaluated <b>{ago(s?.last_eval_ts, now)}</b></>
            : <>15-minute BTC and ETH on Hyperliquid · 1.5% risk, max 3x · last evaluated <b>{ago(s?.last_eval_ts, now)}</b></>}
            {s ? ` · ${s.evals_24h} evaluations / ${s.setups_24h} setups / ${s.fires_24h} fires in 24h` : ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div className="seg">
            {(['off', 'paper', 'live'] as const).map((m) => (
              <button key={m} aria-pressed={s?.requested_mode === m}
                disabled={m === 'live'} aria-disabled={m === 'live'}
                title={m === 'live' ? 'no execution adapter built' : m === 'off' ? 'keeps evaluating; no paper fills' : 'paper fills on every fire'}
                onClick={() => m !== 'live' && modeMut.mutate(m)}>{m}</button>
            ))}
          </div>
          <button className="btn sm" onClick={() => setDrawer(true)}>Parameters</button>
          {!isModel && <button className="btn sm ghost" disabled={btMut.isPending}
            onClick={() => { setBtReport(null); btMut.mutate(false); }}
            title={d?.backtest ? `report from ${new Date(d.backtest.generated_ts * 1000).toLocaleString()} · ${d.backtest.trades} trades / ${d.backtest.evals} evaluations` : 'Coverage-honest replay over the stored candles'}>
            {btMut.isPending ? 'Running…' : 'Backtest'}
          </button>}
        </div>
      </div>

      {btReport !== null && (
        <div className="drawer-wrap" onClick={() => setBtReport(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 980 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, gap: 8 }}>
              <h2>Backtest — {s?.name ?? id} <small className="dim" style={{ fontWeight: 400 }}>{btMeta}</small></h2>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn sm ghost" disabled={btMut.isPending} onClick={() => btMut.mutate(true)}>{btMut.isPending ? 'Replaying…' : 'Re-run now'}</button>
                <button className="btn sm ghost" onClick={() => setBtReport(null)}>Close</button>
              </div>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.5, margin: 0, overflow: 'auto' }}>{btReport}</pre>
          </div>
        </div>
      )}

      {detail.isError && <div className="banner short">Strategies API unavailable — retrying.</div>}
      {modeMut.isError && <div className="banner short">Mode change failed — admin only.</div>}
      {s && s.requested_mode === 'off' && (
        <div className="banner amber">Requested off: this strategy keeps evaluating and logging every candle, but fires are not paper-filled. Switch to paper to collect trades.</div>
      )}
      {latest?.risk_note && (
        <div className="banner amber">Risk engine would have: {latest.risk_note} — annotated only; the paper trade proceeded.</div>
      )}

      {/* Stats strip — counted from closed paper trades in the window */}
      <div className="ctop" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div className="seg">{WINDOWS.map((w) => <button key={w} aria-pressed={win === w} onClick={() => setWin(w)}>{w}</button>)}</div>
        <div className="sub">{stats ? `${stats.trades} closed paper trade(s) in ${win}` : ''}</div>
      </div>
      <div className="riskstrip">
        <div className="rc"><span>Trades</span><b>{stats?.trades ?? '—'}</b></div>
        <div className="rc"><span>Win rate</span><b className={stats?.trades ? '' : 'dim'}>{stats?.trades && stats.win_rate != null ? `${stats.win_rate.toFixed(0)}%` : '—'}</b></div>
        <div className="rc"><span>Profit factor</span><b className={stats?.trades ? '' : 'dim'}>{stats?.trades && stats.pf != null ? (stats.pf >= 999 ? '∞' : stats.pf.toFixed(2)) : '—'}</b></div>
        <div className="rc"><span>Expectancy</span><b className={stats?.trades ? '' : 'dim'}>{stats?.trades && stats.expectancy != null ? usd(stats.expectancy) : '—'}</b><small>net / trade</small></div>
        <div className="rc"><span>Max drawdown</span><b className={stats?.trades ? '' : 'dim'}>{stats?.trades && stats.max_dd != null ? usd(stats.max_dd) : '—'}</b></div>
        <div className="rc"><span>Fill rate</span><b className={s?.fires_total ? '' : 'dim'}>{s?.fires_total ? `${((s.fill_rate ?? 0) * 100).toFixed(0)}%` : '—'}</b><small>{s ? `${s.fills_total} fills / ${s.fires_total} fires` : ''}{s?.fills_taker ? ` · ${s.fills_taker} taker` : ''}{s?.entries_pending ? ` · ${s.entries_pending} resting` : ''}{s?.entries_unfilled ? ` · ${s.entries_unfilled} unfilled` : ''}</small></div>
        <div className="rc"><span>Fee drag</span><b className={stats?.trades ? '' : 'dim'}>{stats?.trades && stats.fee_drag != null ? `${stats.fee_drag.toFixed(1)}%` : '—'}</b><small>of gross{stats?.trades ? ` · net ${usd(stats.net)}` : ''}</small></div>
      </div>

      {/* Main grid: chart + conditions */}
      <div className="sgrid" style={{ marginTop: 12 }}>
        <div className="card tbwrap" style={{ padding: 0, minHeight: 420, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px' }}>
            <h2 style={{ margin: 0 }}>Chart</h2>
            <div className="seg">{(['BTC', 'ETH'] as const).map((a) => <button key={a} aria-pressed={asset === a} onClick={() => setAsset(a)}>{a}</button>)}</div>
          </div>
          <div style={{ flex: 1, minHeight: 360 }}>
            <TradingChart marketId={MARKET_ID[asset]} symbol={asset} />
          </div>
          <div className="sub" style={{ padding: '6px 14px' }}>Live 15m candles. Conditions on the right are the {asset} evaluation from {latest ? ago(latest.ts, now) : '—'}.</div>
        </div>

        {isModel ? (
          <MindPanel asset={asset} latest={latest} waiting={s?.waiting_for_sentence ?? null} apiError={detail.isError} />
        ) : (
        <div className="card condcard">
          <div className="statehdr">
            <span>Current state · {asset}</span>
            <span className={`tag ${latest?.fired ? 'long' : latest?.direction ? 'amber' : 'flat'}`}>{latest?.fired ? 'Fired' : latest?.direction ? `${latest.direction} setup` : 'Waiting'}</span>
          </div>
          <div className="statev">{latest?.reason ?? s?.waiting_for_sentence ?? 'Waiting for first evaluation'}</div>
          {Object.keys(latest?.labels ?? {}).length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {Object.entries(latest!.labels).map(([k, v]) => <span key={k} className="tag flat" style={{ fontSize: 11 }} title={v}>{k}: {v}</span>)}
            </div>
          )}
          <div className="condlist">
            {detail.isError && <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)' }}>Strategies API unavailable — retrying.</div>}
            {!detail.isError && conditions.map((c, i) => (
              <div className="cond" key={i}>
                {condIcon(c)}
                <div className="cbody">
                  <div className="cn">{c.name}
                    {c.warming && <span className={`tag ${c.warming.startsWith('warming') ? 'amber' : 'flat'}`} style={{ marginLeft: 6, fontSize: 10.5, padding: '0 6px' }}>{c.warming}</span>}
                  </div>
                  {c.subline && <div className="cs">{c.subline}</div>}
                </div>
                <div className="cv">{c.value ? `${c.value}${c.threshold ? ` (${c.threshold})` : ''}` : (c.warming ?? 'waiting')}</div>
              </div>
            ))}
            {conditions.length === 0 && <div className="empty">No conditions defined.</div>}
          </div>
          <div className="scoreblk">
            <div className="scoretotal">
              <span>Score</span>
              <b className={latest?.total_score == null ? 'dim' : ''}>
                {latest?.total_score == null ? '—' : latest.total_score.toFixed(2)} / fires at {latest?.fire_threshold?.toFixed(2) ?? '—'}
              </b>
            </div>
            {latest?.components && Object.keys(latest.components).length > 0 && (
              <div className="sub" style={{ marginTop: 2 }}>
                {Object.entries(latest.components).map(([k, v]) => `${k} ${v == null ? 'n/a' : v.toFixed(2)}`).join(' · ')}
              </div>
            )}
            <div className="kv2">
              <div><span>Entry</span><b className={entry ? '' : 'dim'}>{entry?.px != null ? entry.px.toLocaleString() : '—'}</b></div>
              <div><span>Valid / time stop</span><b className={entry ? '' : 'dim'}>{entry ? `${entry.entry_valid_min ?? '—'}m / ${entry.time_stop_h ?? '—'}h` : '—'}</b></div>
              <div><span>Stop</span><b className={entry ? '' : 'dim'}>{entry?.stop != null ? entry.stop.toLocaleString() : '—'}</b></div>
              <div><span>Target 1</span><b className={entry ? '' : 'dim'}>{entry?.target1 != null ? entry.target1.toLocaleString() : '—'}</b></div>
            </div>
          </div>
          {warmLabels.length > 0 && (
            <div className="sub" style={{ marginTop: 4 }}>{warmLabels.join(' · ')}</div>
          )}
        </div>
        )}
      </div>

      {/* Tabs */}
      <div className="tabs" style={{ marginTop: 14 }}>
        {(['log', 'trades', 'breakdown', 'parameters', 'changelog'] as const).map((t) => (
          <button key={t} aria-selected={tab === t} onClick={() => setTab(t)}>{t === 'log' ? 'Evaluation log' : t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      <div className="card" style={{ marginTop: 8 }}>
        {tab === 'log' && <EvalLog rows={d?.recent_signals ?? []} now={now} loading={detail.isLoading} />}
        {tab === 'trades' && <TradeTable open={d?.open_positions ?? []} closed={d?.recent_trades ?? []} now={now} />}
        {tab === 'breakdown' && (isModel
          ? <MindBreakdownTab id={id} data={breakdown.data} loading={breakdown.isLoading} error={breakdown.isError} />
          : <div className="empty">{breakdownName(id)} — {stats?.trades ? `${stats.trades} trade(s) so far; breakdown tables are in the backtest report.` : 'populates once there are closed paper trades to break down.'}</div>)}
        {tab === 'parameters' && (
          <ParamTable params={d?.parameters ?? []} onEdit={() => setDrawer(true)} />
        )}
        {tab === 'changelog' && (
          (changelog.data?.length ?? 0) === 0 ? <div className="empty">No changes recorded yet.</div>
            : <div className="ltable"><table><thead><tr><th>When</th><th>Change</th><th>Reason</th><th>By</th></tr></thead><tbody>
              {changelog.data!.map((c, i) => (
                <tr key={i}><td className="dim">{new Date(c.ts).toLocaleString()}</td><td className="mono">{JSON.stringify(c.diff)}</td><td className="muted">{c.reason}</td><td className="mono dim">{c.wallet?.slice(0, 8)}…</td></tr>
              ))}
            </tbody></table></div>
        )}
      </div>

      {drawer && <ParamDrawer id={id} params={d?.parameters ?? []} onClose={() => setDrawer(false)}
        onSaved={() => qc.invalidateQueries({ queryKey: ['strategy-detail', id] })} />}
    </div>
  );
}

function EvalLog({ rows, now, loading }: { rows: Signal[]; now: number; loading: boolean }) {
  if (!rows.length) return <div className="empty">{loading ? 'Loading evaluations' : 'No evaluations logged yet — the worker is not writing for this strategy.'}</div>;
  return (
    <div className="ltable">
      <div className="sub" style={{ padding: '8px 14px' }}>Last {rows.length} evaluations, newest first · refreshes every 10s</div>
      <table>
        <thead><tr><th>When</th><th>Asset</th><th>Result</th><th>Reason</th><th className="r">{rows.some((x) => x.model) ? 'Conviction' : 'Score'}</th><th>{rows.some((x) => x.model) ? 'Mind' : 'Conditions'}</th></tr></thead>
        <tbody>
          {rows.map((x) => {
            const met = x.conditions.filter((c) => c.met === true).length;
            const evald = x.conditions.filter((c) => c.met != null).length;
            return (
              <tr key={x.id}>
                <td className="dim" title={new Date(x.ts).toLocaleString()}>{ago(x.ts, now)}</td>
                <td>{x.asset}</td>
                <td>{x.fired ? <span className="tag long">fired{x.paper_fill ? ' · paper' : ''}</span>
                  : x.direction ? <span className="tag amber">{x.direction} · not confirmed</span> : <span className="tag flat">skipped</span>}</td>
                <td className="muted" style={{ maxWidth: 520 }}>
                  {x.reason}
                  {x.warming.length > 0 && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{x.warming.join(' · ')}</div>}
                </td>
                {x.model ? (
                  <>
                    <td className="r mono" title={x.raw_conviction != null ? `raw ${x.raw_conviction.toFixed(2)} × multipliers` : ''}>{x.conviction == null ? '—' : x.conviction.toFixed(2)}<span className="dim"> {x.size_tier ?? ''}</span></td>
                    <td className="dim" title={(x.reasons ?? []).map((r) => `${r.key} ${r.strength.toFixed(2)}×${r.weight.toFixed(2)}`).join('\n')}>
                      {(x.reasons ?? []).length} reasons{(x.vetoes ?? []).filter((v) => v.hit).length ? ` · veto ${(x.vetoes ?? []).filter((v) => v.hit).map((v) => v.key).join(', ')}` : ''}{x.level_type ? ` · ${x.level_type}` : ''}
                    </td>
                  </>
                ) : (
                  <>
                    <td className="r mono">{x.total_score == null ? '—' : x.total_score.toFixed(2)}{x.fire_threshold != null ? <span className="dim"> / {x.fire_threshold.toFixed(2)}</span> : null}</td>
                    <td className="dim" title={x.conditions.map((c) => `${c.met === true ? '✓' : c.met === false ? '✕' : '·'} ${c.name}${c.value ? ': ' + c.value : ''}${c.warming ? ' [' + c.warming + ']' : ''}`).join('\n')}>{met}/{evald} met{x.conditions.length - evald ? ` · ${x.conditions.length - evald} unavailable` : ''}</td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TradeTable({ open, closed, now }: { open: Trade[]; closed: Trade[]; now: number }) {
  const rows = [...open, ...closed];
  const model = rows.some((t) => t.model);
  if (!rows.length) return <div className="empty">No paper trades yet. Every fire opens a paper trade here (entry, stop, target, MAE/MFE, fees, net).</div>;
  return (
    <div className="ltable"><table>
      <thead><tr><th>Filled</th><th>Asset</th><th>Side</th><th className="r">Entry</th><th className="r">Stop</th><th className="r">Target</th><th className="r">Size</th><th>Exit</th><th className="r">Net</th><th className="r">MAE / MFE</th>{model && <th className="r" title="R multiple on exit (net / initial risk) · expected hold · lifecycle phase">R · hold</th>}</tr></thead>
      <tbody>{rows.map((t) => (
        <tr key={t.id}>
          <td className="dim">{t.fill_ts ? ago(t.fill_ts, now) : 'pending'}</td>
          <td>{t.asset}</td>
          <td><span className={`tag ${t.direction === 'long' ? 'long' : 'short'}`}>{t.direction}</span></td>
          <td className="r mono">{t.entry_px?.toLocaleString() ?? '—'}</td>
          <td className="r mono">{t.stop_px?.toLocaleString() ?? '—'}</td>
          <td className="r mono">{t.target_px?.toLocaleString() ?? '—'}</td>
          <td className="r mono">{t.size ?? '—'}{t.leverage ? ` · ${t.leverage.toFixed(1)}x` : ''}</td>
          <td className="dim">{t.exit_ts ? `${t.exit_reason ?? 'closed'} · ${ago(t.exit_ts, now)}` : <span className="tag amber">open</span>}</td>
          <td className={`r mono ${t.pnl_net == null ? 'dim' : t.pnl_net >= 0 ? 'long-c' : 'short-c'}`}>{t.pnl_net == null ? '—' : usd(t.pnl_net)}</td>
          <td className="r mono dim">{t.mae == null ? '—' : t.mae.toFixed(2)} / {t.mfe == null ? '—' : t.mfe.toFixed(2)}</td>
          {model && <td className="r mono dim" title={JSON.stringify(t.lifecycle ?? {})}>{t.r_multiple == null ? '—' : `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple.toFixed(2)}R`}{t.expected_hold_min != null ? ` · ${t.expected_hold_min}m` : ''}{t.lifecycle && typeof t.lifecycle.phase === 'string' ? ` · ${t.lifecycle.phase}` : ''}</td>}
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function breakdownName(id: string): string {
  return ({
    '01': 'By asset / cascade size / quiet-time; MFE-before-fill',
    '02': 'By settlement time / crowding source; gauge report',
    '03': 'By cohort wallet; lag to fill; cohort forward P/L',
    '04': 'Back-inside rate; OI on win vs loss; time of day',
    '05': 'By session (EU/US/Monday Asia) and vol-percentile bucket',
    '06': 'Gated vs ungated; timing-layer audit',
    '06u': 'Ungated vs gated; timing-layer audit',
    '05c': 'Chase vs pullback entry (s05): fill rate, taker share, by session',
  } as Record<string, string>)[id] ?? 'Breakdown';
}

const num = (v: unknown): string => (typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : '—');

// Mind panel (doc 17 step 5): one row per reason — name, strength bar, weight,
// contribution — then the vetoes row, the multipliers row, conviction + size
// tier and the thesis. Values are the latest evaluation row for the asset;
// nothing is computed client-side except contribution % formatting.
function MindPanel({ asset, latest, waiting, apiError }: { asset: string; latest: Signal | undefined; waiting: string | null; apiError: boolean }) {
  const reasons: MindReason[] = latest?.reasons ?? [];
  const vetoes = latest?.vetoes ?? [];
  const hit = vetoes.filter((v) => v.hit);
  const mults = (latest?.multipliers ?? []).filter((m) => m.multiplier !== 1);
  const setup = (latest?.setup ?? {}) as Record<string, unknown>;
  const conv = latest?.conviction ?? null;
  const tier = latest?.size_tier ?? null;
  const tierTag = tier === 'full' ? 'long' : tier === 'half' ? 'amber' : 'flat';
  return (
    <div className="card condcard">
      <div className="statehdr">
        <span>Mind · {asset}</span>
        <span className={`tag ${latest?.fired ? 'long' : hit.length ? 'short' : latest?.direction ? 'amber' : 'flat'}`}>
          {latest?.fired ? 'Fired' : hit.length ? 'Vetoed' : latest?.direction ? `${latest.direction} setup` : 'Waiting'}
        </span>
      </div>
      <div className="statev">{latest?.reason || waiting || 'Waiting for first evaluation'}</div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {latest?.level_type && <span className="tag flat" style={{ fontSize: 11 }}>level: {latest.level_type}{latest.level_price != null ? ` @ ${num(latest.level_price)}` : ''}</span>}
        {latest?.day_type && <span className="tag flat" style={{ fontSize: 11 }}>day: {latest.day_type}</span>}
        {latest?.labels?.session && <span className="tag flat" style={{ fontSize: 11 }}>session: {latest.labels.session}</span>}
      </div>
      <div className="condlist">
        {apiError && <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)' }}>Strategies API unavailable — retrying.</div>}
        {!apiError && reasons.map((r) => (
          <div className="cond" key={r.key} title={r.description ?? r.key}>
            <span className={`ci ${r.strength >= 0.5 ? 'ok' : r.strength > 0 ? 'wait' : 'no'}`}>{r.strength >= 0.5 ? '✓' : r.strength > 0 ? '·' : '✕'}</span>
            <div className="cbody">
              <div className="cn">{r.key.replace(/_/g, ' ')}</div>
              <div className="cs" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-block', width: 90, height: 5, borderRadius: 3, background: 'var(--line)', overflow: 'hidden' }}>
                  <i style={{ display: 'block', height: '100%', width: `${Math.max(0, Math.min(1, r.strength)) * 100}%`, background: r.strength >= 0.5 ? 'var(--long)' : 'var(--amber, var(--accent))' }} />
                </span>
                <span>strength {r.strength.toFixed(2)} · weight {r.weight.toFixed(2)}</span>
              </div>
            </div>
            <div className="cv" title="strength × weight / Σweights">{(r.contribution * 100).toFixed(0)}%</div>
          </div>
        ))}
        {!apiError && !reasons.length && <div className="empty">{latest ? 'No setup detected on this evaluation — reasons appear once a level/setup is found.' : 'No evaluation yet for this asset.'}</div>}
        {vetoes.length > 0 && (
          <div className="cond" style={{ alignItems: 'flex-start' }}>
            <span className={`ci ${hit.length ? 'no' : 'ok'}`}>{hit.length ? '✕' : '✓'}</span>
            <div className="cbody">
              <div className="cn">Vetoes</div>
              <div className="cs" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {vetoes.map((v) => <span key={v.key} className={`tag ${v.hit ? 'short' : 'flat'}`} style={{ fontSize: 10.5, padding: '0 6px' }} title={v.text ?? v.key}>{v.key.replace(/_/g, ' ')}</span>)}
              </div>
              {hit.length > 0 && <div className="cs" style={{ marginTop: 3 }}>{hit.map((v) => v.text ?? v.key).join(' · ')}</div>}
            </div>
            <div className="cv">{hit.length ? `${hit.length} hit` : 'none hit'}</div>
          </div>
        )}
        {(latest?.multipliers ?? []).length > 0 && (
          <div className="cond" style={{ alignItems: 'flex-start' }}>
            <span className={`ci ${mults.length ? 'wait' : 'ok'}`}>{mults.length ? '·' : '✓'}</span>
            <div className="cbody">
              <div className="cn">Multipliers</div>
              <div className="cs">{mults.length ? mults.map((m) => `${m.key.replace(/_/g, ' ')} ×${m.multiplier.toFixed(2)}`).join(' · ') : `all ×1.00 (${(latest?.multipliers ?? []).length} checked)`}</div>
            </div>
            <div className="cv">×{(latest?.multipliers ?? []).reduce((p, m) => p * m.multiplier, 1).toFixed(2)}</div>
          </div>
        )}
      </div>
      <div className="scoreblk">
        <div className="scoretotal">
          <span>Conviction</span>
          <b className={conv == null ? 'dim' : ''}>
            {conv == null ? '—' : conv.toFixed(2)}
            {latest?.raw_conviction != null && conv != null && Math.abs(latest.raw_conviction - conv) > 0.005 ? <span className="dim"> (raw {latest.raw_conviction.toFixed(2)})</span> : null}
            {tier && <span className={`tag ${tierTag}`} style={{ marginLeft: 8 }}>{tier === 'none' ? 'no size' : `${tier} size`}</span>}
          </b>
        </div>
        <div className="sub" style={{ marginTop: 2 }}>skip &lt; 0.55 · half 0.55–0.70 · full ≥ 0.70 · any veto skips</div>
        {latest?.thesis && <div className="sub" style={{ marginTop: 6, color: 'var(--text)' }} title="thesis — the plain-language reason the model would take (or is taking) this trade">{latest.thesis}</div>}
        <div className="kv2">
          <div><span>Entry</span><b className={setup.entry != null ? '' : 'dim'}>{num(setup.entry)}</b></div>
          <div><span>Stop</span><b className={setup.stop != null ? '' : 'dim'}>{num(setup.stop)}</b></div>
          <div><span>Target 1</span><b className={setup.t1 != null ? '' : 'dim'}>{num(setup.t1)}</b></div>
          <div><span>Target 2</span><b className={setup.t2 != null ? '' : 'dim'}>{num(setup.t2)}</b></div>
        </div>
      </div>
    </div>
  );
}

// Breakdown tab for a model: live weights, calibration table by conviction
// bucket, R by size tier and the weight history the weekly learner wrote.
function MindBreakdownTab({ id, data, loading, error }: { id: string; data: import('@/types/strategies').MindBreakdown | undefined; loading: boolean; error: boolean }) {
  if (error) return <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)' }}>Breakdown unavailable — retrying.</div>;
  if (loading || !data) return <div className="empty">Loading breakdown for {id}</div>;
  const tiers = Object.entries(data.r_by_tier);
  const learn = Object.entries(data.learn_flags);
  return (
    <div>
      <div className="riskstrip" style={{ margin: '10px 14px 0' }}>
        <div className="rc"><span>Closed trades</span><b>{data.trades_closed}</b><small>with an R multiple</small></div>
        <div className="rc"><span>Mean R</span><b className={data.mean_r == null ? 'dim' : data.mean_r >= 0 ? 'long-c' : 'short-c'}>{data.mean_r == null ? '—' : `${data.mean_r >= 0 ? '+' : ''}${data.mean_r.toFixed(2)}R`}</b></div>
        {tiers.map(([tier, v]) => (
          <div className="rc" key={tier}><span>{tier} size</span><b className={v.mean_r >= 0 ? 'long-c' : 'short-c'}>{`${v.mean_r >= 0 ? '+' : ''}${v.mean_r.toFixed(2)}R`}</b><small>{v.trades} trades · {(v.win_rate * 100).toFixed(0)}% win</small></div>
        ))}
        <div className="rc"><span>Learning</span><b className="dim">{learn.length ? learn.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(' · ') : 'off (60-day gate)'}</b><small>weights change only via the weekly learner</small></div>
      </div>

      <div className="ltable" style={{ marginTop: 10 }}>
        <div className="sub" style={{ padding: '8px 14px' }}>Reason weights — live values the Mind multiplies each reason's strength by</div>
        {data.weights.length === 0 ? <div className="empty">No weight rows yet.</div> : (
          <table><thead><tr><th>Reason</th><th className="r">Weight</th><th>Description</th><th>Updated</th></tr></thead><tbody>
            {data.weights.map((w) => (
              <tr key={w.reason_key}><td className="mono">{w.reason_key}</td><td className="r amt">{w.weight.toFixed(2)}</td><td className="muted">{w.reason_text}</td><td className="dim">{w.updated_at ? new Date(w.updated_at).toLocaleString() : '—'}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>

      <div className="ltable" style={{ marginTop: 10 }}>
        <div className="sub" style={{ padding: '8px 14px' }}>Execution and rule splits (spec v1.3) — closed paper trades by whether the 0.5 ATR stop floor moved the stop{['M1', 'M5'].includes(data.model) ? ' and by reclaim type (same-candle sweep+reclaim confirmed by one more close vs reclaim on a later candle)' : ''}; post-only entries rejected as crossing and re-quoted one tick inside</div>
        {(data.splits ?? []).length === 0 && !data.execution ? <div className="empty">No v1.3 rows yet.</div> : (
          <>
            <table><thead><tr><th>Split</th><th className="r">Trades</th><th className="r">Win rate</th><th className="r">Mean R</th></tr></thead><tbody>
              {(data.splits ?? []).map((s, i) => (
                <tr key={i}><td className="mono">{s.label}</td><td className="r">{s.trades}</td>
                  <td className="r">{s.win_rate == null ? '—' : `${(s.win_rate * 100).toFixed(0)}%`}</td>
                  <td className={`r mono ${s.mean_r == null ? 'dim' : s.mean_r >= 0 ? 'long-c' : 'short-c'}`}>{s.mean_r == null ? '—' : `${s.mean_r >= 0 ? '+' : ''}${s.mean_r.toFixed(2)}R`}</td></tr>
              ))}
            </tbody></table>
            {data.execution && (
              <div className="riskstrip" style={{ margin: '10px 14px' }}>
                <div className="rc"><span>Rejected + re-quoted</span><b>{data.execution.requoted_fraction == null ? '—' : `${(data.execution.requoted_fraction * 100).toFixed(1)}%`}</b><small>{data.execution.requoted} of {data.execution.takes} entries · {data.execution.requoted_filled} filled</small></div>
                <div className="rc"><span>Fill − original limit</span><b className={data.execution.fill_minus_original == null ? 'dim' : ''}>{data.execution.fill_minus_original == null ? '—' : `${data.execution.fill_minus_original >= 0 ? '+' : ''}${data.execution.fill_minus_original.toFixed(4)}`}</b><small>{data.execution.fill_minus_original_bps == null ? 'no filled re-quoted trade yet' : `${data.execution.fill_minus_original_bps >= 0 ? '+' : ''}${data.execution.fill_minus_original_bps.toFixed(2)} bps mean, filled re-quoted trades`}</small></div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="ltable" style={{ marginTop: 10 }}>
        <div className="sub" style={{ padding: '8px 14px' }}>Liquidation normalisers — calibrated daily 00:05 UTC from the trailing 180 days (spec v1.1); "—" = not calibrated, model uses the fixed OI fraction</div>
        {(data.normalisers ?? []).length === 0 ? <div className="empty">No calibration rows yet — the worker computes them on its first 15m boundary.</div> : (
          <table><thead><tr><th>Coin</th><th>Key</th><th className="r">Value</th><th className="r">Samples</th><th className="r">Window</th><th>Computed</th><th>Used by</th><th>Note</th></tr></thead><tbody>
            {data.normalisers.map((n, i) => (
              <tr key={i}><td className="mono">{n.coin}</td><td className="mono">{n.key}</td>
                <td className={`r amt ${n.value == null ? 'dim' : ''}`}>{n.value == null ? '—' : n.key === 'live_coverage' ? `${(n.value * 100).toFixed(1)}%` : `$${Math.round(n.value).toLocaleString()}`}</td>
                <td className="r">{n.sample_count}</td><td className="r">{n.window_days == null ? '—' : `${n.window_days.toFixed(0)}d`}</td>
                <td className="dim">{n.computed_at ? new Date(n.computed_at).toLocaleString() : '—'}</td>
                <td className="muted">{n.used_by}</td><td className="dim">{n.note ?? ''}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>

      <div className="ltable" style={{ marginTop: 10 }}>
        <div className="sub" style={{ padding: '8px 14px' }}>Calibration — closed trades by conviction bucket per week (does higher conviction win more?)</div>
        {data.calibration.length === 0 ? <div className="empty">No calibration rows yet — the weekly pass writes them once there are closed paper trades.</div> : (
          <table><thead><tr><th>Week</th><th>Conviction bucket</th><th className="r">Trades</th><th className="r">Win rate</th><th className="r">Mean R</th></tr></thead><tbody>
            {data.calibration.map((c, i) => (
              <tr key={i}><td className="mono">{c.week}</td><td>{c.bucket}</td><td className="r">{c.trades}</td><td className="r">{c.win_rate == null ? '—' : `${(c.win_rate * 100).toFixed(0)}%`}</td><td className={`r mono ${c.mean_r == null ? 'dim' : c.mean_r >= 0 ? 'long-c' : 'short-c'}`}>{c.mean_r == null ? '—' : `${c.mean_r >= 0 ? '+' : ''}${c.mean_r.toFixed(2)}R`}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>

      <div className="ltable" style={{ marginTop: 10 }}>
        <div className="sub" style={{ padding: '8px 14px' }}>Weight history — every change the weekly learner made (wallet mind-learn)</div>
        {data.weight_history.length === 0 ? <div className="empty">No weight changes yet.</div> : (
          <table><thead><tr><th>When</th><th>Change</th><th>Reason</th></tr></thead><tbody>
            {data.weight_history.map((h, i) => (
              <tr key={i}><td className="dim">{new Date(h.ts).toLocaleString()}</td><td className="mono">{JSON.stringify(h.diff)}</td><td className="muted">{h.reason}</td></tr>
            ))}
          </tbody></table>
        )}
      </div>
    </div>
  );
}

function ParamTable({ params, onEdit }: { params: Parameter[]; onEdit: () => void }) {
  if (!params.length) return <div className="empty">No parameters.</div>;
  return (
    <div className="ltable">
      <table><thead><tr><th>Parameter</th><th className="r">Value</th><th>Unit</th><th className="r">Default</th></tr></thead><tbody>
        {params.map((p) => (
          <tr key={p.key}><td className="mono">{p.key}</td><td className="r amt">{p.value}</td><td className="dim">{p.unit}</td><td className="r dim">{p.default}</td></tr>
        ))}
      </tbody></table>
      <div style={{ padding: 10 }}><button className="btn sm" onClick={onEdit}>Edit a parameter</button></div>
    </div>
  );
}

function ParamDrawer({ id, params, onClose, onSaved }: { id: string; params: Parameter[]; onClose: () => void; onSaved: () => void }) {
  const [key, setKey] = useState(params[0]?.key ?? '');
  const [value, setValue] = useState('');
  const [reason, setReason] = useState('');
  const [err, setErr] = useState('');
  const [ok, setOk] = useState(false);
  const save = async () => {
    setErr('');
    if (!reason.trim()) { setErr('A reason is required.'); return; }
    try {
      await patchParameter(id, key, value, reason.trim());
      setOk(true); onSaved();
    } catch (e: any) {
      setErr(e?.response?.status === 401 || e?.response?.status === 403 ? 'Admin only.' : 'Save failed.');
    }
  };
  return (
    <div className="drawer-wrap" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <h2>Parameters — strategy {id}</h2>
        <div className="sub">Change one parameter. A reason is required and written to the changelog.</div>
        <label className="dlabel">Parameter</label>
        <select className="dinput" value={key} onChange={(e) => { setKey(e.target.value); setValue(''); }}>
          {params.map((p) => <option key={p.key} value={p.key}>{p.key} (now {p.value}{p.unit ? ` ${p.unit}` : ''})</option>)}
        </select>
        <label className="dlabel">New value</label>
        <input className="dinput" value={value} onChange={(e) => setValue(e.target.value)} placeholder={params.find((p) => p.key === key)?.value ?? ''} />
        <label className="dlabel">Reason (required)</label>
        <input className="dinput" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this change?" />
        {err && <div className="banner short" style={{ marginTop: 8 }}>{err}</div>}
        {ok && <div className="banner" style={{ marginTop: 8, background: 'var(--long-soft)', color: 'var(--long)' }}>Saved to changelog.</div>}
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button className="btn primary sm" onClick={save} disabled={!value}>Save</button>
          <button className="btn sm ghost" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
