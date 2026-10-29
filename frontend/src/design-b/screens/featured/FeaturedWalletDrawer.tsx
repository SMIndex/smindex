import { useEffect, useMemo, useRef, useState } from 'react';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist } from '@/hooks/useCopyV1';
import { useMarkets } from '@/hooks/useMarkets';
import AutoCopyButton from '@/components/copy/AutoCopyButton';
import LiveCopyModal, { type LiveCopyPosition } from '@/components/copy/LiveCopyModal';
import { getFeaturedWallet, getReplay, type ClosedTrade, type FeaturedRow, type OpenPosition, type Replay, type WalletDetail } from '@/lib/featuredApi';
import { agoWords, holdWords, labelWords, pct, price, shortAddr, usd } from '@/lib/featuredView';
import { Arrow, Drawer, LabelChips, LiveDot, VenueBadge } from './bits';
import FeaturedCopySheet from './FeaturedCopySheet';

// Wallet detail drawer (design/WalletDetail.dc.html), same drawer on phone.

const CHECK_WORDS: Record<string, (v: any) => string> = {
  role: (v) => `Role: ${v === 'user' ? 'user (not vault, agent or sub-account)' : v}`,
  sample: (v) => `Sample: ${v?.trips ?? '?'} trades over ${v?.days ?? '?'} days`,
  mm: (v) => `Not a market maker: ${v?.maker_share != null ? Math.round(v.maker_share * 100) : '?'}% maker fills, hold ${holdWords(v?.median_hold_sec)}`,
  farmer: (v) => `Not a farmer: PnL ${v != null ? (v * 100).toFixed(2) : '?'}% of volume`,
  bot: (v) => `Not a bot: at most ${v?.trips_per_day_max ?? '?'} trades a day`,
  hedger: (v) => (v === false ? 'No hedge found across venues or spot' : v === true ? 'Hedge found' : 'Hedge check: not yet run (cohort sampler)'),
  wash: (v) => (v == null ? 'Wash: no counterparty data yet' : `Wash: top counterparty ${Math.round(v * 100)}% of fills`),
  liquidations: (v) => `${v} liquidation${v === 1 ? '' : 's'} in 90 days`,
  net_profit: (v) => `Net profit ${usd(v?.usd, true)}${v?.pct_eq != null ? ` (${v.pct_eq}% of equity)` : ''}`,
  worst_trade: (v) => `Worst trade ${usd(v?.usd)}${v?.pct_eq != null ? ` (${Math.abs(v.pct_eq).toFixed(1)}% of equity)` : ''}`,
  drawdown: (v) => `Max drawdown ${v != null ? v.toFixed(1) : '?'}%`,
  label: (v) => `Win rate ${v?.win_rate != null ? Math.round(v.win_rate * 100) : '?'}%, profit factor ${v?.pf != null ? Number(v.pf).toFixed(2) : '?'}`,
  recent: (v) => `Last trade ${v ? agoWords(v) : 'not seen'}`,
  history: (v) => `History: ${v}`,
};
const CHECK_ORDER = ['role', 'sample', 'mm', 'farmer', 'bot', 'hedger', 'wash', 'liquidations', 'net_profit', 'worst_trade', 'drawdown', 'label', 'recent', 'history'];

function fmtUtc(iso: string): string {
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).replace(',', '');
}

function EquityCurve({ points }: { points: WalletDetail['equity_curve'] }) {
  if (!points.length) return <div className="ft-empty">No closed trades in the last 90 days</div>;
  const W = 600, H = 160, pad = 10;
  const t0 = points[0].t, t1 = points[points.length - 1].t || t0 + 1;
  const vs = points.map((p) => p.v);
  const lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
  const x = (t: number) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const y = (v: number) => H - 25 - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - 40);
  const d = `${x(t0)},${y(0)} ` + points.map((p) => `${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
  const fmtD = (t: number) => new Date(t).toLocaleDateString('en-GB', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 160, marginTop: 8 }} role="img" aria-label="Equity curve, cumulative profit of the closed trades with every trade marked">
      <line x1={pad} x2={W - pad} y1={y(0)} y2={y(0)} stroke="var(--line)" strokeDasharray="3 3" />
      <polyline fill="none" stroke="var(--accent)" strokeWidth="2" points={d} />
      {points.map((p) => <circle key={p.id} cx={x(p.t)} cy={y(p.v)} r="3" fill={p.win ? 'var(--long)' : 'var(--short)'}><title>{`${fmtD(p.t)}: ${usd(p.pnl, true)} (total ${usd(p.v, true)})`}</title></circle>)}
      <text x={pad} y={H - 5} fontSize="10">{fmtD(t0)}</text>
      <text x={W - pad} y={H - 5} fontSize="10" textAnchor="end">{fmtD(t1)}</text>
      <text x={pad} y={12} fontSize="10">{usd(hi, true, true)}</text>
    </svg>
  );
}

function hourColor(n: number, max: number): string {
  if (!max || !n) return 'var(--s2)';
  const f = n / max;
  return f > 0.8 ? 'var(--accent)' : f > 0.55 ? 'color-mix(in srgb, var(--accent) 75%, var(--s1))' : f > 0.3 ? 'color-mix(in srgb, var(--accent) 50%, var(--s1))' : 'color-mix(in srgb, var(--accent) 25%, var(--s1))';
}

function localHours(): Set<number> {
  // the user's 20:00 to 04:00 local, in UTC hours
  const off = -new Date().getTimezoneOffset() / 60;
  const out = new Set<number>();
  for (let h = 20; h < 28; h++) out.add((((h - off) % 24) + 24) % 24);
  return out;
}

export default function FeaturedWalletDrawer({ row, onClose, liveAt }: { row: FeaturedRow; onClose: () => void; liveAt?: string | null }) {
  const { isAuthenticated, address } = useAuth();
  const watchlist = useWatchlist(isAuthenticated);
  const { activeMarkets } = useMarkets();
  const [d, setD] = useState<WalletDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [mirror, setMirror] = useState<LiveCopyPosition | null>(null);
  const [margin, setMargin] = useState(50);
  const [lev, setLev] = useState(3);
  const [stop, setStop] = useState<number | null>(null);
  const [rep, setRep] = useState<Replay | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    let on = true;
    getFeaturedWallet(row.venue, row.wallet, 1, 50).then((x) => {
      if (!on) return;
      setD(x);
      setMargin(x.replay_defaults.margin_usd);
      setLev(x.replay_defaults.leverage);
      setStop(x.replay_defaults.stop_pct);
      setRep(x.replay);
    }).catch((e) => on && setErr(e?.response?.data?.detail || 'Could not load this wallet'));
    return () => { on = false; };
  }, [row.venue, row.wallet]);

  // live fields refresh every 15 s while the drawer is open
  useEffect(() => {
    const t = setInterval(() => {
      getFeaturedWallet(row.venue, row.wallet, page, showAll ? 200 : 50).then((x) => setD((cur) => (cur ? { ...cur, ...x, replay: cur.replay } : x))).catch(() => {});
    }, 15_000);
    return () => clearInterval(t);
  }, [row.venue, row.wallet, page, showAll]);

  const recompute = (m: number, l: number, s: number | null) => {
    const n = ++seq.current;
    getReplay(row.venue, row.wallet, m, l, s).then((r) => { if (seq.current === n) setRep(r); }).catch(() => {});
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);

  const mine = useMemo(localHours, []);
  const watched = watchlist.isWatched(row.wallet, row.venue);
  const toggleWatch = async () => {
    if (!isAuthenticated) return;
    if (watched) await watchlist.unwatch(row.wallet, row.venue); else await watchlist.watch(row.wallet, row.venue);
  };
  const marketFor = (coin: string) => activeMarkets.find((m) => m.symbol.toUpperCase() === coin.toUpperCase());
  const mirrorPos = (p: OpenPosition) => {
    const m = marketFor(p.coin);
    if (!m) return;
    setMirror({ market_id: m.market_id, symbol: m.symbol, side: p.side, entry_price: p.entry ?? p.mark ?? 0, mark_price: p.mark ?? p.entry ?? 0,
      leverage: p.leverage ?? 1, size: p.size, unrealized_pnl: p.unrealized, venue: row.venue });
  };
  const trades: ClosedTrade[] = d?.closed_trades ?? [];
  const weekly = d?.weekly ?? [];
  const maxWeek = Math.max(1, ...weekly.map((w) => Math.abs(w.pnl)));
  const hours = d?.hours_utc ?? [];
  const maxHour = Math.max(0, ...hours);
  const lastTrade = liveAt ?? d?.last_trade_at ?? row.last_trade_at;
  const perCoin = (d?.coin_stats ?? []).slice(0, 4).map((c) => `${c.coin} ${c.trades} trades ${c.win_rate != null ? Math.round(c.win_rate * 100) : '?'}% ${usd(c.net, true, true)}`).join(' · ');

  return (
    <Drawer onClose={onClose} label={`Wallet ${shortAddr(row.wallet)}`}>
      <div className="dhead">
        <div className="who">
          <div className="idline">
            <VenueBadge venue={row.venue} />
            <span className="addr" title={row.wallet}>{shortAddr(row.wallet)}</span>
            {row.display_name && <span className="sub">{row.display_name}</span>}
            <LabelChips label={row.label} oneBigWin={row.one_big_win} inactive={row.inactive} history={row.history_from} />
            {row.score != null && <span className="chip chip-grey" title={d ? `Score inputs: ${Object.entries(d.score_inputs).map(([k, v]) => `${k} ${typeof v === 'number' ? v.toFixed(2) : v}`).join(', ')}` : ''}>Score {Math.round(row.score)}</span>}
          </div>
          <div className="verdict">{row.verdict}</div>
          <div className="sub" style={{ marginTop: 6 }}>
            {row.account_value != null && <>Account value {usd(row.account_value)} · </>}
            last trade {agoWords(lastTrade)} · metrics as of {row.as_of ?? 'n/a'} 02:00 UTC
            {row.venue === 'perpl' && ' · single-venue view'}
          </div>
        </div>
        <div className="dacts">
          <button className="btn primary" onClick={() => setCopyOpen(true)}>Copy this trader</button>
          <button className="btn" aria-pressed={watched} onClick={toggleWatch} disabled={!isAuthenticated} title={isAuthenticated ? '' : 'Sign in to watch'}>{watched ? 'Watching' : 'Watch'}</button>
          {row.venue === 'hl' && <AutoCopyButton leaderWallet={row.wallet} leaderName={row.display_name ?? undefined} className="btn" />}
          <button className="btn" aria-label="Close" onClick={onClose}>✕</button>
        </div>
      </div>

      {err && <div className="ft-empty" style={{ color: 'var(--short)' }}>{err}</div>}

      <div className="ft-card replay">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
          <div>
            <div className="lab">If you had copied this wallet, last 90 days</div>
            <div className="big">
              {rep ? <><span className={rep.pnl >= 0 ? 'pos' : 'neg'}>{usd(rep.pnl, true)}</span> on ${margin} margin · worst dip {usd(rep.worst_dip)} · {rep.stopped_out} of {rep.trades} trades stopped out</> : <span className="sub">computing…</span>}
            </div>
            <div className="sub" style={{ marginTop: 4 }}>Entry 1.5 s after the trader, Perpl taker fee included, {rep?.skipped ?? 0} trades skipped ({rep?.skipped ? 'coins not on Perpl' : 'all coins on Perpl'}){rep?.mae_from_candles != null && rep.trades ? ` · stop checked against candles on ${rep.mae_from_candles} trades` : ''}</div>
          </div>
          <div className="ctl">
            <span className="sub">Margin</span><input type="number" min={1} value={margin} onChange={(e) => { const v = Math.max(1, Number(e.target.value) || 1); setMargin(v); recompute(v, lev, stop); }} aria-label="Margin" />
            <span className="sub">Leverage</span><input type="number" min={1} max={50} value={lev} onChange={(e) => { const v = Math.min(50, Math.max(1, Number(e.target.value) || 1)); setLev(v); recompute(margin, v, stop); }} aria-label="Leverage" />
            <span className="sub">Stop %</span><input type="number" min={0} step={0.1} value={stop ?? ''} placeholder="none" onChange={(e) => { const v = e.target.value === '' ? null : Math.max(0, Number(e.target.value)); setStop(v); recompute(margin, lev, v); }} aria-label="Stop loss percent" />
            {d?.replay_defaults.stop_pct != null && <span className="sub">(avg loss {d.replay_defaults.stop_pct}%)</span>}
          </div>
        </div>
      </div>

      <div className="six">
        <div className="ft-card"><div className="lab">Win rate</div><div className="v">{pct((row.win_rate_90d ?? 0) * 100)}</div><div className="sub">of {row.trades_90d} · 30 d {row.win_rate_30d != null ? pct(row.win_rate_30d * 100) : 'n/a'} <Arrow dir={row.momentum?.win_rate} /></div></div>
        <div className="ft-card"><div className="lab">Net profit</div><div className={`v ${(row.net_pnl_90d ?? 0) >= 0 ? 'pos' : 'neg'}`}>{usd(row.net_pnl_90d, true)}</div><div className="sub">30 d {usd(row.net_pnl_30d, true, true)} <Arrow dir={row.momentum?.pnl} /> · all time {usd(row.net_pnl_all, true, true)}</div></div>
        <div className="ft-card"><div className="lab">Profit factor</div><div className="v">{row.profit_factor_90d != null ? (row.profit_factor_90d >= 999 ? 'no losses' : row.profit_factor_90d.toFixed(1)) : 'n/a'}</div><div className="sub">30 d {row.profit_factor_30d != null ? (row.profit_factor_30d >= 999 ? 'no losses' : row.profit_factor_30d.toFixed(1)) : 'n/a'} <Arrow dir={row.momentum?.pf} /></div></div>
        <div className="ft-card"><div className="lab">Avg win / loss</div><div className="v"><span className="pos">{usd(row.avg_win_usd)}</span> / <span className="neg">{usd(row.avg_loss_usd)}</span></div><div className="sub">{row.avg_win_pct_eq != null ? `${pct(row.avg_win_pct_eq, 1)} / ${pct(row.avg_loss_pct_eq, 1)} of equity` : 'equity unknown'}</div></div>
        <div className="ft-card"><div className="lab">Max drawdown</div><div className="v">{pct(row.max_drawdown_90d)}</div><div className="sub">{row.dd_start && row.dd_end ? `${row.dd_start} to ${row.dd_end}` : 'on the 90-day curve'}</div></div>
        <div className="ft-card"><div className="lab">Copiable</div><div className="v">{row.copiable_pct != null ? pct(row.copiable_pct) : 'n/a'}</div><div className="sub">of profit on Perpl coins</div></div>
      </div>

      <div className="two">
        <div className="ft-card">
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><div className="lab">Equity, 90 days · every closed trade marked</div><div className="sub"><span className="inline-dot" style={{ background: 'var(--long)' }} />win <span className="inline-dot" style={{ background: 'var(--short)', marginLeft: 8 }} />loss</div></div>
          {d ? <EquityCurve points={d.equity_curve} /> : <div className="skl" style={{ width: '100%', height: 160, marginTop: 8 }} />}
          <div className="sub" style={{ marginTop: 4 }}>Cumulative realized profit of the closed trades, net of fees and funding. Deposits and withdrawals are not in this line.</div>
        </div>
        <div className="ft-card">
          <div className="lab">Weekly result, last 13 weeks</div>
          <div className="wk">
            {weekly.length ? weekly.map((w, i) => (
              <div key={i} style={{ height: `${Math.max(3, (Math.abs(w.pnl) / maxWeek) * 100)}%`, background: w.pnl >= 0 ? 'var(--long)' : 'var(--short)' }}
                title={`Week ending ${new Date(w.end).toLocaleDateString('en-GB', { month: 'short', day: 'numeric' })}: ${usd(w.pnl, true)} over ${w.n} trades`} />
            )) : <div className="skl" style={{ width: '100%' }} />}
          </div>
          <div className="sub" style={{ marginTop: 6 }}>{row.profitable_weeks_13 ?? '?'} of 13 weeks profitable · longest losing streak {row.longest_loss_streak ?? '?'} trades{row.loss_streak_at ? ` (${row.loss_streak_at})` : ''}</div>
        </div>
      </div>

      <div className="three">
        <div className="ft-card">
          <div className="lab">Risk</div>
          <div className="kv"><span>Worst trade</span><span className="neg">{usd(row.worst_trade_usd)}{row.worst_trade_pct_eq != null ? ` (${Math.abs(row.worst_trade_pct_eq).toFixed(1)}%)` : ''}</span></div>
          <div className="kv"><span>Worst day</span><span className="neg">{usd(row.worst_day_usd)}{row.worst_day_usd != null && row.avg_equity_90d ? ` (${(Math.abs(row.worst_day_usd) / row.avg_equity_90d * 100).toFixed(1)}%)` : ''}</span></div>
          <div className="kv"><span>Worst week</span><span className={(row.worst_week_usd ?? 0) < 0 ? 'neg' : ''}>{usd(row.worst_week_usd)}</span></div>
          <div className="kv"><span>Liquidations</span><span>{row.liquidations_90d}</span></div>
          <div className="kv"><span>Max leverage used</span><span>{row.max_leverage != null ? `${row.max_leverage}x` : 'not read'}</span></div>
          <div className="kv"><span>Largest trade share of profit</span><span>{row.largest_trade_share != null ? pct(row.largest_trade_share * 100) : 'n/a'}</span></div>
          {!!d?.big_losses_year?.length && <div className="sub" style={{ marginTop: 6 }}>Trades over 10% of equity: {d.big_losses_year.map((t) => `${t.coin} ${usd(t.pnl_usd)} (${t.close_time.slice(0, 10)})`).join(', ')}</div>}
        </div>
        <div className="ft-card">
          <div className="lab">Behaviour</div>
          <div className="kv"><span>Long / short</span><span>{row.long_share != null ? `${Math.round(row.long_share * 100)}% / ${Math.round((1 - row.long_share) * 100)}%` : 'n/a'}</span></div>
          <div className="kv"><span>Median hold</span><span>{holdWords(row.median_hold_sec)}</span></div>
          <div className="kv"><span>Trades per week</span><span>{row.trades_per_week?.toFixed(1) ?? 'n/a'}</span></div>
          <div className="kv"><span>Maker entries</span><span>{row.maker_share != null ? pct(row.maker_share * 100) : 'n/a'}</span></div>
          <div className="kv"><span>Averages down</span><span>{row.averages_down_n != null ? `${row.averages_down_n > row.trades_90d / 3 ? 'Often' : 'Rarely'} (${row.averages_down_n} of ${row.trades_90d})` : 'n/a'}</span></div>
          <div className="kv"><span>Adds to winners</span><span>{row.adds_to_winner_n != null ? `${row.adds_to_winner_n > row.trades_90d / 3 ? 'Often' : 'Rarely'} (${row.adds_to_winner_n} of ${row.trades_90d})` : 'n/a'}</span></div>
        </div>
        <div className="ft-card">
          <div className="lab">When it trades, UTC (your 20:00 to 04:00 marked)</div>
          <div className="hm">{Array.from({ length: 24 }, (_, h) => <div key={h} className={mine.has(h) ? 'mine' : ''} style={{ background: hourColor(hours[h] ?? 0, maxHour) }} title={`${String(h).padStart(2, '0')}:00 UTC: ${hours[h] ?? 0} entries`} />)}</div>
          <div className="sub" style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between' }}><span>0h</span><span>entry times of the 90-day trades</span><span>23h</span></div>
        </div>
      </div>

      <div className="ft-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}><div className="lab">Open positions now ({d?.open_positions?.length ?? row.open_count}) · live</div><LiveDot at={d?.positions_at ?? row.positions_at} maxSec={600} /></div>
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          {(d?.open_positions?.length ?? 0) === 0 ? <div className="sub">No open position{d?.positions_at ? '' : ' read yet (the trades feed reads it within seconds of the first trade)'}.</div> : (
            <table className="ft-table">
              <thead><tr><th>Coin</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>Unrealized</th><th>Lev</th><th>Liquidation</th><th></th></tr></thead>
              <tbody>
                {d!.open_positions.map((p) => {
                  const away = p.liq_px && p.mark ? Math.abs(p.liq_px - p.mark) / p.mark * 100 : null;
                  const m = marketFor(p.coin);
                  return (
                    <tr key={p.coin}>
                      <td>{p.coin}</td><td className={p.side === 'long' ? 'pos' : 'neg'}>{p.side === 'long' ? 'Long' : 'Short'}</td><td>{usd(p.size_usd)}</td>
                      <td className="mono">{price(p.entry)}</td><td className="mono">{price(p.mark)}</td>
                      <td className={p.unrealized >= 0 ? 'pos' : 'neg'}>{usd(p.unrealized, true)}{p.roe != null ? ` (${(p.roe * 100).toFixed(1)}%)` : ''}</td>
                      <td>{p.leverage ? `${p.leverage}x` : 'n/a'}</td>
                      <td className="mono">{price(p.liq_px)}{away != null && <span className="sub"> ({away.toFixed(0)}% away)</span>}</td>
                      <td>{m ? <button className="btn" style={{ height: 28 }} onClick={() => mirrorPos(p)}>Mirror at my size</button> : <span className="sub">not on Perpl</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="ft-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}><div className="lab">Closed trades, newest first ({d?.closed_total ?? row.trades_90d} in 90 days)</div><div className="sub">{perCoin && `Per coin: ${perCoin}`}</div></div>
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table className="ft-table">
            <thead><tr><th>Coin</th><th>Side</th><th>Opened</th><th>Closed</th><th>Hold</th><th>Size</th><th>Entry</th><th>Exit</th><th>Fills</th><th>Lev</th><th>Fees</th><th>Result</th></tr></thead>
            <tbody>
              {trades.slice(0, showAll ? trades.length : 8).map((t) => (
                <tr key={t.id}>
                  <td>{t.coin}</td><td className={t.side === 'long' ? 'pos' : 'neg'}>{t.side === 'long' ? 'Long' : 'Short'}</td>
                  <td>{fmtUtc(t.open_time)}</td><td>{fmtUtc(t.close_time)}</td><td>{holdWords(t.hold_sec)}</td><td>{usd(t.max_size_usd)}</td>
                  <td className="mono">{price(t.avg_entry)}</td><td className="mono">{price(t.avg_exit)}</td>
                  <td>{t.n_adds ? `${t.n_adds} add${t.n_adds > 1 ? 's' : ''}, ` : '1 open, '}{t.n_closes} close{t.n_closes === 1 ? '' : 's'}</td>
                  <td>{t.leverage ? `${t.leverage}x` : 'n/a'}</td><td>{usd(t.fees)}</td>
                  <td className={t.pnl_usd >= 0 ? 'pos' : 'neg'}>{usd(t.pnl_usd, true)} ({t.pnl_pct_margin != null ? pct(t.pnl_pct_margin, 1) : `${pct(t.ret_pct, 2)} move`}){t.liquidation ? ' liquidated' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!showAll && trades.length > 8 && <div className="sub" style={{ marginTop: 8 }}><button className="btn sm ghost" style={{ padding: 0, color: 'var(--accent-text)' }} onClick={() => setShowAll(true)}>Show all {d?.closed_total ?? trades.length}</button></div>}
        {showAll && d && d.closed_total > trades.length && (
          <div className="sub" style={{ marginTop: 8, display: 'flex', gap: 8 }}>
            <button className="btn sm" disabled={page <= 1} onClick={() => { const p = page - 1; setPage(p); getFeaturedWallet(row.venue, row.wallet, p, 50).then(setD).catch(() => {}); }}>Newer</button>
            <button className="btn sm" onClick={() => { const p = page + 1; setPage(p); getFeaturedWallet(row.venue, row.wallet, p, 50).then(setD).catch(() => {}); }}>Older</button>
            <span>page {page}</span>
          </div>
        )}
      </div>

      <div className="ft-card">
        <div className="lab">Why this wallet is here: checks passed (measured values)</div>
        <div className="checks">
          {d ? CHECK_ORDER.filter((k) => d.flags[k]).map((k) => {
            const f = d.flags[k];
            const w = CHECK_WORDS[k] ? CHECK_WORDS[k](f.value) : `${k}: ${JSON.stringify(f.value)}`;
            return <div key={k}><span className={`chip ${f.pass === true ? 'chip-ok' : f.pass === false ? 'chip-fail' : 'chip-grey'}`}>{f.pass === true ? '✓' : f.pass === false ? '✕' : '?'}</span><span>{w}</span></div>;
          }) : <span className="skl" style={{ width: 200 }} />}
        </div>
        {d?.identity?.sub_accounts?.length ? <div className="sub" style={{ marginTop: 8 }}>Sub-accounts folded into this record: {d.identity.sub_accounts.map(shortAddr).join(', ')}</div> : null}
      </div>

      {copyOpen && d && <FeaturedCopySheet row={row} defaults={d.replay_defaults} replay={rep} onClose={() => setCopyOpen(false)} />}
      {mirror && (
        <LiveCopyModal isOpen onClose={() => setMirror(null)} traderWallet={row.wallet} traderName={row.display_name} position={mirror} />
      )}
      {!address && null}
    </Drawer>
  );
}

export { labelWords };
