import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getStrategies, getRisk, getFeedHealth, getRecentSignals, getOpenTrades, staleFeeds, numFromSid, EVIDENCE_ORDER,
} from '@/lib/strategiesApi';
import type { StrategyState, SignalRow, Trade } from '@/types/strategies';

// Design B — Strategies overview. Presentation only, from /api/strategies/*.
// Every number is COUNTED server-side from strat_signals / strat_trades; text
// comes from state (waiting_for_sentence, last_eval_ts, warming labels).
// Paper only — Live is disabled: no execution adapter built. Nothing trades.

const dash = (v: number | null | undefined, fmt?: (n: number) => string) =>
  (v == null ? '—' : (fmt ? fmt(v) : String(v)));
const usd = (n: number) => `${n < 0 ? '-' : ''}$${Math.abs(n) >= 1000 ? (Math.abs(n) / 1000).toFixed(2) + 'K' : Math.abs(n).toFixed(2)}`;
const pct = (n: number) => `${n.toFixed(0)}%`;
const pf = (n: number) => (n >= 999 ? '∞' : n.toFixed(2));

export function ago(ts: number | null | undefined, now: number): string {
  if (!ts) return 'never';
  const s = Math.max(0, Math.round((now - ts) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), intervalMs); return () => clearInterval(t); }, [intervalMs]);
  return now;
}
export const shortName = (sid: string) => `${numFromSid(sid)} ${sid.replace(/^[sm]\d+[a-z]?_/, '').replace(/_/g, ' ')}`;

function statusPill(s: StrategyState) {
  if (s.effective_mode === 'live') return <span className="tag long">Live</span>;
  if (s.effective_mode === 'paper') return <span className="tag amber">Paper</span>;
  return <span className="tag flat" title={s.effective_reason ?? ''}>Evaluating only</span>;
}

export default function StrategiesOverviewB() {
  const navigate = useNavigate();
  const now = useNow();
  const strategies = useQuery({ queryKey: ['strategies'], queryFn: getStrategies, refetchInterval: 10_000 });
  const risk = useQuery({ queryKey: ['strategies-risk'], queryFn: getRisk, refetchInterval: 10_000 });
  const feeds = useQuery({ queryKey: ['strategies-feeds'], queryFn: getFeedHealth, refetchInterval: 30_000 });
  const signals = useQuery({ queryKey: ['strategies-signals-recent'], queryFn: () => getRecentSignals(24, 300), refetchInterval: 10_000 });
  const openTrades = useQuery({ queryKey: ['strategies-open-trades'], queryFn: getOpenTrades, refetchInterval: 10_000 });
  const [sigFilter, setSigFilter] = useState<'all' | 'fired' | 'skipped'>('all');

  const rows = (strategies.data ?? []).slice().sort(
    (a, b) => EVIDENCE_ORDER.indexOf(numFromSid(a.id)) - EVIDENCE_ORDER.indexOf(numFromSid(b.id)));
  const stale = staleFeeds(feeds.data ?? []);
  const r = risk.data;
  // Honest API state: never an infinite "loading". The queries keep retrying
  // (refetchInterval), so a failure reads "unavailable — retrying".
  const apiError = strategies.isError || feeds.isError || risk.isError;
  const stamp = apiError ? 'unavailable' : (strategies.isLoading ? 'loading' : 'live');
  const effGlobal: 'off' | 'paper' | 'live' =
    rows.some((s) => s.effective_mode === 'live') ? 'live'
      : rows.some((s) => s.effective_mode === 'paper') ? 'paper' : 'off';
  const newest = rows.reduce((m, s) => Math.max(m, s.last_eval_ts ?? 0), 0);
  const evalsTotal = rows.reduce((n, s) => n + s.evals_24h, 0);
  const firesTotal = rows.reduce((n, s) => n + s.fires_24h, 0);
  const sigRows = (signals.data ?? []).filter((x) => sigFilter === 'all' ? true : sigFilter === 'fired' ? x.fired : !x.fired);

  return (
    <div className="screen">
      <div className="head">
        <div>
          <h1>Strategies</h1>
          <p>{rows.length ? `${rows.filter((s) => !s.model).length} paper strategies + ${rows.filter((s) => !!s.model).length} heuristic-mind models` : 'Paper instances'} on 15-minute BTC and ETH — every condition evaluates on the data that exists today; short history is labelled "warming", never blocked. Models carry a <span className="tag accent" style={{ fontSize: 11, padding: '0 6px' }}>Model</span> badge: deterministic Mind reasons, vetoes and conviction instead of a score.</p>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
            <div className="seg">
              <button aria-pressed={effGlobal === 'paper'}>Paper</button>
              <button disabled title="no execution adapter built" aria-disabled="true">Live</button>
            </div>
            <div className="gmode-reason">
              {effGlobal === 'paper' ? `Global mode: Paper · ${rows.length} evaluating · last evaluation ${ago(newest || null, now)}`
                : effGlobal === 'live' ? 'Global mode: Live' : (rows.length ? 'Evaluating only — no paper fills requested' : 'Loading state')}
            </div>
          </div>
          <div className="stamp"><i style={apiError ? { background: 'var(--short)' } : undefined} />Hyperliquid · {stamp}</div>
        </div>
      </div>

      {stale.length > 0 && (
        <div className="banner amber">Hyperliquid feed is behind ({stale.map((f) => `${f.feed} ${Math.round((f.age_s || 0))}s`).join(', ')}). Conditions that read it carry "unavailable (not in score)" until it catches up.</div>
      )}
      {apiError && (
        <div className="empty" style={{ borderColor: 'var(--short)', color: 'var(--short)', marginTop: 10 }}>
          Strategies API unavailable — retrying.
        </div>
      )}

      {/* Risk engine strip — paper equity + real paper P/L; rules annotate, never block */}
      <div className="riskstrip">
        <div className="rc"><span>Paper equity</span><b>{dash(r?.equity_usd ?? null, usd)}</b><small>start {r ? usd(r.paper_equity_start) : '—'} · P/L {r ? usd(r.paper_pnl_total) : '—'}</small></div>
        <div className="rc">
          <span>Today vs 4% cap</span>
          <b className={(r?.daily_realized_pnl ?? 0) >= 0 ? 'long-c' : 'short-c'}>{r ? usd(r.daily_realized_pnl) : '—'}</b>
          <div className="capbar"><i style={{ width: capPct(r), background: capColor(r) }} /></div>
          <small>annotated, not enforced (paper)</small>
        </div>
        <div className="rc"><span>Open paper positions</span><b>{dash(r?.open_positions_count ?? null)}</b><small>limit 2 would apply live</small></div>
        <div className="rc"><span>Risk per trade</span><b>1.5%{r?.equity_usd ? ` · ${usd(r.equity_usd * 0.015)}` : ''}</b><small>max 3x</small></div>
        <div className="rc"><span>Consecutive losses</span><b>{dash(r?.consecutive_losses ?? null)}</b><small>2 would pause 4h live</small></div>
        <div className="rc"><span>Fees 30d</span><b>{r ? usd(r.paper_fees_month) : '—'}</b><small>{r && r.paper_gross_month > 0 ? `${((r.paper_fees_month / r.paper_gross_month) * 100).toFixed(1)}% of gross` : 'of gross'}</small></div>
      </div>

      {/* Strategies table */}
      <div className="card" style={{ padding: 0, marginTop: 12 }}>
        <div className="ltable">
          <table>
            <thead><tr>
              <th style={{ width: 34 }}>#</th><th>Strategy</th><th>Status</th><th>Waiting for</th>
              <th className="r" title="evaluations in the last 24h / with a direction identified / fired">Evals · setups · fires 24h</th>
              <th className="r">Trades 30d</th><th className="r">Win rate</th>
              <th className="r">PF</th><th className="r">Net 30d</th><th className="r">Open</th>
            </tr></thead>
            <tbody>
              {strategies.isError ? (
                <tr><td colSpan={10}><div className="empty">Strategies API unavailable — retrying.</div></td></tr>
              ) : strategies.isLoading ? (
                <tr><td colSpan={10}><div className="empty">Loading strategies</div></td></tr>
              ) : rows.map((s) => {
                const n = numFromSid(s.id);
                const warmN = s.warming.filter((w) => w.includes('warming')).length;
                const unavN = s.warming.filter((w) => w.includes('unavailable')).length;
                return (
                  <tr key={s.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/strategies/${n}`)}>
                    <td className="dim">{n}</td>
                    <td>
                      <div className="name">{s.name}{s.model && <span className="tag accent" style={{ marginLeft: 6, fontSize: 11, padding: '0 6px' }} title={`heuristic-mind model ${s.model} — conviction, not score`}>Model</span>}</div>
                      <div className="meta">
                        last evaluated {ago(s.last_eval_ts, now)}
                        {warmN > 0 && <span className="tag amber" style={{ marginLeft: 6, fontSize: 11, padding: '0 6px' }} title={s.warming.filter((w) => w.includes('warming')).join('\n')}>warming {warmN}</span>}
                        {unavN > 0 && <span className="tag flat" style={{ marginLeft: 4, fontSize: 11, padding: '0 6px' }} title={s.warming.filter((w) => w.includes('unavailable')).join('\n')}>unavailable {unavN}</span>}
                        {Object.entries(s.labels).map(([k, v]) => <span key={k} className="tag flat" style={{ marginLeft: 4, fontSize: 11, padding: '0 6px' }} title={v}>{k}: {v.length > 28 ? v.slice(0, 28) + '…' : v}</span>)}
                      </div>
                    </td>
                    <td>{statusPill(s)}</td>
                    <td className="muted">{s.waiting_for_sentence ?? 'Waiting for first evaluation'}</td>
                    <td className="r mono">{s.evals_24h} · {s.setups_24h} · <b className={s.fires_24h ? 'long-c' : ''}>{s.fires_24h}</b></td>
                    <td className="r">{s.trades_30d}</td>
                    <td className="r">{s.trades_30d ? dash(s.win_rate_30d, pct) : <span className="dim">—</span>}</td>
                    <td className="r">{s.trades_30d ? dash(s.pf_30d, pf) : <span className="dim">—</span>}</td>
                    <td className={`r ${s.trades_30d ? ((s.net_30d ?? 0) >= 0 ? 'long-c' : 'short-c') : 'dim'}`}>{s.trades_30d ? dash(s.net_30d, usd) : '—'}</td>
                    <td className="r">{s.open_positions}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Open paper positions */}
      <div className="card" style={{ marginTop: 12 }}>
        <h2>Open paper positions</h2>
        {(openTrades.data?.length ?? 0) === 0 ? (
          <div className="empty">No open paper positions. {firesTotal ? `${firesTotal} fire(s) in the last 24h — see Recent signals for how each was filled or expired.` : `${evalsTotal} evaluations in the last 24h, none fired yet.`}</div>
        ) : (
          <div className="ltable"><table>
            <thead><tr><th>Strategy</th><th>Asset</th><th>Side</th><th className="r">Entry</th><th className="r">Stop</th><th className="r">Target</th><th className="r">Size</th><th className="r">Lev</th><th>Filled</th></tr></thead>
            <tbody>{openTrades.data!.map((t: Trade) => (
              <tr key={t.id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/strategies/${numFromSid(t.strategy)}`)}>
                <td>{shortName(t.strategy)}</td><td>{t.asset}</td>
                <td><span className={`tag ${t.direction === 'long' ? 'long' : 'short'}`}>{t.direction}</span></td>
                <td className="r mono">{t.entry_px?.toLocaleString() ?? '—'}</td><td className="r mono">{t.stop_px?.toLocaleString() ?? '—'}</td>
                <td className="r mono">{t.target_px?.toLocaleString() ?? '—'}</td><td className="r mono">{t.size ?? '—'}</td><td className="r">{t.leverage ? `${t.leverage.toFixed(1)}x` : '—'}</td>
                <td className="dim">{t.fill_ts ? ago(t.fill_ts, now) : 'pending'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      {/* Recent signals: every evaluation, collapsed */}
      <div className="card" style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div>
            <h2>Recent signals</h2>
            <div className="sub">Last 24 hours — every evaluation (fired, skipped, not confirmed) with its reason and score; consecutive identical skips collapsed with a count</div>
          </div>
          <div className="seg">
            {(['all', 'fired', 'skipped'] as const).map((f) => <button key={f} aria-pressed={sigFilter === f} onClick={() => setSigFilter(f)}>{f}</button>)}
          </div>
        </div>
        {signals.isError ? <div className="empty" style={{ marginTop: 10 }}>Strategies API unavailable — retrying.</div>
          : sigRows.length === 0 ? <div className="empty" style={{ marginTop: 10 }}>{signals.isLoading ? 'Loading evaluations' : sigFilter === 'fired' ? 'No fires in the last 24h.' : 'No evaluations logged in the last 24h — the worker is not writing.'}</div>
            : <SignalTable rows={sigRows} now={now} onOpen={(sid) => navigate(`/strategies/${numFromSid(sid)}`)} />}
      </div>
    </div>
  );
}

export function SignalTable({ rows, now, onOpen }: { rows: SignalRow[]; now: number; onOpen?: (sid: string) => void }) {
  return (
    <div className="ltable" style={{ marginTop: 10 }}>
      <table>
        <thead><tr><th>When</th><th>Strategy</th><th>Asset</th><th>Result</th><th>Reason</th><th className="r">Score</th><th className="r">Count</th></tr></thead>
        <tbody>
          {rows.map((x, i) => (
            <tr key={`${x.strategy}-${x.asset}-${x.ts_last}-${i}`} style={onOpen ? { cursor: 'pointer' } : undefined} onClick={() => onOpen?.(x.strategy)}>
              <td className="dim" title={new Date(x.ts_last).toLocaleString()}>{ago(x.ts_last, now)}{x.count > 1 ? <div style={{ fontSize: 11 }}>since {ago(x.ts_first, now)}</div> : null}</td>
              <td>{shortName(x.strategy)}</td>
              <td>{x.asset}</td>
              <td>{x.fired ? <span className="tag long">fired{x.paper_fill ? ' · paper' : ''}</span>
                : x.direction ? <span className="tag amber">{x.direction} · not confirmed</span> : <span className="tag flat">skipped</span>}</td>
              <td className="muted" style={{ maxWidth: 520 }}>
                {x.reason}
                {x.warming.length > 0 && <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 2 }}>{x.warming.join(' · ')}</div>}
              </td>
              <td className="r mono">{x.total_score == null ? '—' : x.total_score.toFixed(2)}</td>
              <td className="r dim">{x.count > 1 ? `×${x.count}` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function capPct(r: any): string {
  if (!r || !r.equity_usd || r.equity_usd <= 0) return '0%';
  const cap = r.equity_usd * 0.04;
  const used = Math.max(0, -r.daily_realized_pnl);
  return `${Math.min(100, (used / cap) * 100).toFixed(0)}%`;
}
function capColor(r: any): string {
  const p = parseFloat(capPct(r));
  return p > 80 ? 'var(--short)' : p > 50 ? 'var(--amber)' : 'var(--long)';
}
