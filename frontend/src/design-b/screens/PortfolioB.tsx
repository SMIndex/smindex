import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import api, { getCopyPerformance } from '@/lib/api';
import { useCopyStore } from '@/stores/copyStore';
import { shortenAddress, formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';

// Design B — Portfolio (spec §3.5). Presentation only: the EXACT existing data
// layer (GET /api/account-health, /api/account-health/equity-curve, Export PnL
// blob, copy store, getCopyPerformance). Deposit/Withdraw are not surfaced in
// shell B (spec). Every figure is real; missing data uses the honest empty
// block / "Not enough data" per wrapper Rule 4 — nothing is invented.

const RISK_WORD: Record<string, string> = { safe: 'Low', warning: 'Medium', danger: 'High', critical: 'Critical' };
const RISK_POS: Record<string, number> = { safe: 0.16, warning: 0.5, danger: 0.8, critical: 0.95 };
const RISK_CLS: Record<string, string> = { safe: 'long-c', warning: 'amber-c', danger: 'short-c', critical: 'short-c' };

function polar(cx: number, cy: number, r: number, deg: number) {
  const a = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

// Donut composition ring (account value = available + margin + unrealized magnitudes)
function AccountRing({ available, margin, unrealized }: { available: number; margin: number; unrealized: number }) {
  const parts = [
    { v: Math.max(0, available), color: 'var(--accent)' },
    { v: Math.max(0, margin), color: 'var(--amber)' },
    { v: Math.abs(unrealized), color: unrealized >= 0 ? 'var(--long)' : 'var(--short)' },
  ];
  const total = parts.reduce((s, p) => s + p.v, 0) || 1;
  const r = 52, C = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg viewBox="0 0 120 120">
      <circle cx="60" cy="60" r={r} fill="none" stroke="var(--s3)" strokeWidth="14" />
      {parts.map((p, i) => {
        const len = (p.v / total) * C;
        const el = (
          <circle key={i} cx="60" cy="60" r={r} fill="none" stroke={p.color} strokeWidth="14"
            strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset} />
        );
        offset += len;
        return el;
      })}
    </svg>
  );
}

function RiskGauge({ level }: { level: string }) {
  const pos = RISK_POS[level] ?? 0.16;            // 0 = safe (left), 1 = liq (right)
  const deg = 180 - pos * 180;                     // 180°..0°
  const [nx, ny] = polar(100, 100, 74, 180 + pos * 180); // needle tip
  const arc = (from: number, to: number, color: string) => {
    const [x1, y1] = polar(100, 100, 88, from);
    const [x2, y2] = polar(100, 100, 88, to);
    return `M ${x1} ${y1} A 88 88 0 0 1 ${x2} ${y2}`;
  };
  void deg;
  return (
    <svg viewBox="0 0 200 110">
      <path d={arc(180, 120, 'a')} fill="none" stroke="var(--long)" strokeWidth="12" strokeLinecap="round" opacity=".85" />
      <path d={arc(120, 60, 'b')} fill="none" stroke="var(--amber)" strokeWidth="12" opacity=".85" />
      <path d={arc(60, 0, 'c')} fill="none" stroke="var(--short)" strokeWidth="12" strokeLinecap="round" opacity=".85" />
      <line x1="100" y1="100" x2={nx} y2={ny} stroke="var(--text)" strokeWidth="3" strokeLinecap="round" />
      <circle cx="100" cy="100" r="5" fill="var(--text)" />
    </svg>
  );
}

function WinRing({ pct }: { pct: number | null }) {
  const r = 32, C = 2 * Math.PI * r;
  const frac = pct == null ? 0 : Math.max(0, Math.min(1, pct / 100));
  return (
    <svg viewBox="0 0 80 80">
      <circle cx="40" cy="40" r={r} fill="none" stroke="var(--s3)" strokeWidth="8" />
      {pct != null && <circle cx="40" cy="40" r={r} fill="none" stroke="var(--accent)" strokeWidth="8" strokeLinecap="round" strokeDasharray={`${frac * C} ${C}`} />}
    </svg>
  );
}

type DayPoint = { date: string; equity: number; delta: number };

function useDailySeries(days: number, enabled: boolean) {
  const q = useQuery({
    queryKey: ['equity-curve', 'dsb-portfolio', days],
    queryFn: () => api.get(`/api/account-health/equity-curve?days=${days}`).then((r) => r.data),
    enabled,
    refetchInterval: 60000,
  });
  const series: DayPoint[] = useMemo(() => {
    const raw: { timestamp: string; equity: number }[] = q.data || [];
    const byDay = new Map<string, number>();
    for (const s of raw) byDay.set(new Date(s.timestamp).toISOString().slice(0, 10), s.equity);
    const dates = [...byDay.keys()].sort();
    const out: DayPoint[] = [];
    let prev: number | null = null;
    for (const d of dates) {
      const eq = byDay.get(d)!;
      out.push({ date: d, equity: eq, delta: prev == null ? 0 : eq - prev });
      prev = eq;
    }
    return out;
  }, [q.data]);
  return { series, loading: q.isLoading };
}

const PILLS = ['positions', 'orders', 'trades', 'copyh', 'copyp', 'funding'] as const;
type Pill = typeof PILLS[number];
const PILL_LABEL: Record<Pill, string> = {
  positions: 'Positions', orders: 'Open orders', trades: 'Trade history',
  copyh: 'Copy history', copyp: 'Copy performance', funding: 'Funding',
};

export default function PortfolioB() {
  const { address, hydrated } = useAuth();
  const copies = useCopyStore((s) => s.copies);
  const [dailyDays, setDailyDays] = useState(14);
  const [pill, setPill] = useState<Pill>('positions');

  const { data: healthData, isLoading } = useQuery({
    queryKey: ['account-health', address],
    queryFn: () => api.get('/api/account-health').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 5000,
  });
  const { series: daily, loading: dailyLoading } = useDailySeries(dailyDays, !!address);
  const perf = useQuery({ queryKey: ['copy-performance'], queryFn: getCopyPerformance, enabled: !!address });

  if (!hydrated) return <div className="screen"><div className="empty">Loading…</div></div>;
  if (!address) {
    return (
      <div className="screen">
        <div className="head"><div><h1>Portfolio</h1><p>Connect your wallet to view your portfolio</p></div></div>
        <div className="empty">Connect your wallet to view portfolio.</div>
      </div>
    );
  }
  if (isLoading) return <div className="screen"><div className="head"><div><h1>Portfolio</h1></div></div><div className="empty">Loading your account…</div></div>;

  const connected = healthData?.connected;
  const account = connected ? healthData.account : { equity: 0, balance: 0, available: 0, margin_used: 0, margin_ratio_pct: 0, account_leverage: 0, total_unrealized_pnl: 0, total_notional: 0, position_count: 0 };
  const risk = connected ? healthData.risk : { level: 'safe', closest_liq_pct: null, closest_liq_market: null, positions_with_sl: 0, positions_with_tp: 0, unprotected_positions: 0 };
  const positions = connected ? healthData.positions : [];

  const move5 = account.total_notional * 0.05;
  const move10 = account.total_notional * 0.10;
  const pipRow = (covered: number, open: number) => (
    <div className="pips">{Array.from({ length: 5 }).map((_, i) => <i key={i} className={open > 0 && i < Math.round((covered / open) * 5) ? 'on' : ''} />)}</div>
  );

  // Trading record — honestly scoped to copied trades (no account-wide trade-stats endpoint exists)
  const perfRows: any[] = perf.data || [];
  const rec = perfRows.reduce((a, l) => {
    a.trades += l.total_copies || 0; a.closed += l.closed_copies || 0;
    a.volume += l.total_volume || 0; a.net += l.total_pnl || 0;
    a.wins += ((l.win_rate || 0) / 100) * (l.closed_copies || 0);
    return a;
  }, { trades: 0, closed: 0, volume: 0, net: 0, wins: 0 });
  const winRate = rec.closed > 0 ? (rec.wins / rec.closed) * 100 : null;
  const hasRec = rec.trades > 0;

  const exportPnl = () => {
    api.get('/api/export/pnl', { responseType: 'blob' }).then((res) => {
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url; a.download = `pnl_${address?.slice(0, 10)}_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click(); window.URL.revokeObjectURL(url);
    });
  };

  const maxAbs = Math.max(1, ...daily.map((d) => Math.abs(d.delta)));
  const eqMin = Math.min(...daily.map((d) => d.equity), 0), eqMax = Math.max(...daily.map((d) => d.equity), 1);

  return (
    <div className="screen">
      <div className="head">
        <div><h1>Portfolio</h1><p><span className="mono">{shortenAddress(address)}</span> on Perpl, Monad</p></div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="stamp"><i />Live</span>
          <button className="btn sm" onClick={exportPnl}>Export PnL</button>
        </div>
      </div>

      {!connected && (
        <div className="empty" style={{ marginBottom: 12, textAlign: 'left' }}>
          No Perpl exchange account found for this wallet yet. Your copied-trade record still appears below.
        </div>
      )}

      {/* Row 1 — bento */}
      <div className="bento">
        <div className="card bal">
          <div className="ring">
            <AccountRing available={account.available} margin={account.margin_used} unrealized={account.total_unrealized_pnl} />
            <div className="ringc"><div className="v">{formatUSD(account.equity)}</div><div className="l">Account value</div></div>
          </div>
          <div className="ringlegend">
            <div><i style={{ background: 'var(--accent)' }} />Available<b>{formatUSD(account.available)}</b></div>
            <div><i style={{ background: 'var(--amber)' }} />Margin in use<b>{formatUSD(account.margin_used)}</b></div>
            <div><i style={{ background: account.total_unrealized_pnl >= 0 ? 'var(--long)' : 'var(--short)' }} />Unrealized<b className={account.total_unrealized_pnl >= 0 ? 'long-c' : 'short-c'}>{account.total_unrealized_pnl >= 0 ? '+' : ''}{formatUSD(account.total_unrealized_pnl)}</b></div>
          </div>
          <div className={`delta ${account.total_unrealized_pnl >= 0 ? 'long-c' : 'short-c'}`}>
            {account.total_unrealized_pnl === 0 ? 'No open unrealized result' : `${account.total_unrealized_pnl >= 0 ? 'Up' : 'Down'} ${formatUSD(Math.abs(account.total_unrealized_pnl))} unrealized`}
          </div>
        </div>

        <div className="card">
          <h2>Risk right now</h2>
          <div className="sub">How close open positions are to liquidation</div>
          <div className="gaugewrap">
            <RiskGauge level={risk.level} />
            <div className="gaugetxt"><b className={RISK_CLS[risk.level]}>{RISK_WORD[risk.level] ?? 'Low'}</b><span>{account.position_count > 0 ? (risk.closest_liq_pct != null ? `Closest liquidation ${risk.closest_liq_pct.toFixed(1)}% away` : `${account.position_count} open`) : 'No open positions'}</span></div>
          </div>
          <div className="stress">
            <div><span>Leverage</span><b>{account.account_leverage.toFixed(2)}x</b></div>
            <div><span>Margin usage</span><b>{account.margin_ratio_pct.toFixed(2)}%</b></div>
            <div><span>5% move against you</span><b className={account.total_notional > 0 ? 'short-c' : 'dim'}>{account.total_notional > 0 ? `-${formatUSD(move5)}` : 'No impact'}</b></div>
            <div><span>10% move against you</span><b className={account.total_notional > 0 ? 'short-c' : 'dim'}>{account.total_notional > 0 ? `-${formatUSD(move10)}` : 'No impact'}</b></div>
          </div>
          <div className="cover"><span>Stop loss cover</span>{pipRow(risk.positions_with_sl, account.position_count)}<b>{risk.positions_with_sl} of {account.position_count}</b></div>
          <div className="cover"><span>Take profit cover</span>{pipRow(risk.positions_with_tp, account.position_count)}<b>{risk.positions_with_tp} of {account.position_count}</b></div>
        </div>

        <div className="card">
          <h2>Trading record</h2>
          <div className="sub">From your copied trades on Perpl</div>
          {hasRec ? (
            <>
              <div className="recgrid">
                <div className="wr"><WinRing pct={winRate} /><div><b>{winRate != null ? `${winRate.toFixed(1)}%` : 'Not enough data'}</b><span>Win rate</span></div></div>
                <div className="rkv"><b>{rec.trades.toLocaleString()}</b><span>Trades</span></div>
                <div className="rkv"><b>{formatUSD(rec.volume)}</b><span>Volume</span></div>
                <div className="rkv"><b>{rec.trades > 0 ? formatUSD(rec.volume / rec.trades) : 'Not enough data'}</b><span>Average trade</span></div>
                <div className="rkv"><b className={rec.net >= 0 ? 'long-c' : 'short-c'}>{rec.net >= 0 ? '+' : ''}{formatUSD(rec.net)}</b><span>Net result</span></div>
                <div className="rkv"><b className={rec.net >= 0 ? 'long-c' : 'short-c'}>{rec.trades > 0 ? `${rec.net >= 0 ? '+' : ''}${formatUSD(rec.net / rec.trades)}` : '—'}</b><span>Per trade</span></div>
              </div>
            </>
          ) : (
            <div className="empty" style={{ marginTop: 12 }}>No copied-trade record yet. Trades you copy from Perpl or Hyperliquid traders appear here with win rate, volume and net result.</div>
          )}
        </div>
      </div>

      {/* Row 2 — daily result + calendar */}
      <div className="row two" style={{ marginTop: 12 }}>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
            <div><h2>Daily result</h2><div className="sub" style={{ margin: 0 }}>Change in account value each day, with account value as dots</div></div>
            <div className="seg">{[['7 days', 7], ['14 days', 14], ['30 days', 30], ['All', 365]].map(([l, d]) => <button key={d as number} aria-pressed={dailyDays === d} onClick={() => setDailyDays(d as number)}>{l}</button>)}</div>
          </div>
          {dailyLoading ? <div className="empty">Loading daily result…</div>
            : daily.length < 2 ? <div className="empty">No account history yet. Snapshots are taken every 4 hours while you hold positions.</div>
              : (
                <svg viewBox="0 0 600 220" style={{ width: '100%', height: 220, display: 'block' }}>
                  {daily.map((d, i) => {
                    const bw = 600 / daily.length, x = i * bw + bw * 0.2, w = bw * 0.6;
                    const h = (Math.abs(d.delta) / maxAbs) * 80;
                    const y = d.delta >= 0 ? 110 - h : 110;
                    const ey = 200 - ((d.equity - eqMin) / (eqMax - eqMin || 1)) * 180;
                    return (
                      <g key={i}>
                        <rect x={x} y={y} width={w} height={Math.max(1, h)} fill={d.delta >= 0 ? 'var(--long)' : d.delta < 0 ? 'var(--short)' : 'var(--dim)'} rx="2" />
                        <circle cx={x + w / 2} cy={ey} r="2" fill="var(--accent)" />
                      </g>
                    );
                  })}
                  <line x1="0" y1="110" x2="600" y2="110" stroke="var(--line)" strokeWidth="1" />
                </svg>
              )}
        </div>
        <div className="card">
          <h2>Trading calendar</h2>
          <div className="sub">Last six weeks. Darker green is a bigger up day, red a down day.</div>
          <Calendar series={daily} />
          <div className="heatlegend"><span>Less</span><i style={{ background: 'var(--s2)' }} /><i style={{ background: 'var(--long-soft)' }} /><i style={{ background: 'var(--long)', opacity: .6 }} /><i style={{ background: 'var(--long)' }} /><span>More</span><span style={{ marginLeft: 12 }}><i style={{ background: 'var(--short)', display: 'inline-block', verticalAlign: 'middle' }} /> Loss</span></div>
        </div>
      </div>

      {/* Row 3 — pill switcher */}
      <div style={{ marginTop: 12 }}>
        <div className="seg" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
          {PILLS.map((p) => <button key={p} aria-pressed={pill === p} onClick={() => setPill(p)}>{p === 'positions' ? `Positions (${positions.length})` : PILL_LABEL[p]}</button>)}
        </div>

        {pill === 'positions' && (
          positions.length === 0 ? <div className="empty">No open positions. Anything you open on the terminal or through copy trade appears here with size, entry, mark, result and liquidation distance.</div>
            : <div className="cards">{positions.map((p: any) => {
              const cfg = MARKETS[p.market_id]; const dec = cfg?.decimals ?? 2;
              return (
                <div className="tc" key={p.market_id}>
                  <div className="top"><div><div className="name">{p.symbol} <span className={`tag ${p.side === 'long' ? 'long' : 'short'}`}>{p.side === 'long' ? 'Long' : 'Short'} {p.leverage.toFixed(0)}x</span></div><div className="meta">Liq ${formatPrice(p.liq_price, dec)} · {p.liq_distance_pct?.toFixed(1)}% away</div></div></div>
                  <div className="pnl"><div className="v" style={{ fontSize: 20 }}><span className={p.total_pnl >= 0 ? 'long-c' : 'short-c'}>{p.total_pnl >= 0 ? '+' : ''}{formatUSD(p.total_pnl)}</span><small>Unrealized · {p.roe_pct >= 0 ? '+' : ''}{p.roe_pct.toFixed(1)}% ROE</small></div></div>
                  <div className="facts"><div><b>{p.size.toFixed((cfg as any)?.sizeDecimals ?? 4)}</b><span>Size</span></div><div><b>{formatUSD(p.deposit)}</b><span>Margin</span></div><div><b>${formatPrice(p.entry_price, dec)}</b><span>Entry</span></div><div><b>${formatPrice(p.mark_price, dec)}</b><span>Mark</span></div></div>
                  <div style={{ display: 'flex', gap: 6 }}><span className="tag flat" style={{ opacity: p.has_sl ? 1 : .4 }}>SL {p.has_sl ? 'set' : 'none'}</span><span className="tag flat" style={{ opacity: p.has_tp ? 1 : .4 }}>TP {p.has_tp ? 'set' : 'none'}</span></div>
                </div>
              );
            })}</div>
        )}

        {pill === 'orders' && <div className="empty">Resting limit orders you place on the terminal will be listed here.</div>}
        {pill === 'trades' && <div className="empty">Your filled trades will be listed here as they settle on Perpl.</div>}

        {pill === 'copyh' && (
          copies.length === 0 ? <div className="empty">Every order placed from copy trade will be listed here with the trader it mirrored.</div>
            : <div className="ltable"><table><thead><tr><th>Asset</th><th>Side</th><th className="r">Margin</th><th>From</th><th className="r">When</th></tr></thead><tbody>
              {copies.map((c, i) => (
                <tr key={i}><td className="amt">{c.symbol}</td><td><span className={`tag ${c.side === 'long' ? 'long' : 'short'}`}>{c.side === 'long' ? 'Long' : 'Short'} {c.leverage}x</span></td><td className="r">{formatUSD(c.amount_usd)}</td><td className="mono dim">{shortenAddress(c.copied_from)}</td><td className="r dim">{new Date(c.timestamp).toLocaleString()}</td></tr>
              ))}
            </tbody></table></div>
        )}

        {pill === 'copyp' && (
          perf.isLoading ? <div className="empty">Loading copy performance…</div>
            : perfRows.length === 0 ? <div className="empty">No copied trades yet. Once you copy a trader, their per-trader win rate, volume and net result appear here.</div>
              : <div style={{ display: 'grid', gap: 10 }}>{perfRows.map((l) => (
                <div className="card" key={l.leader_wallet} style={{ padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <div><span className="mono" style={{ fontWeight: 600 }}>{shortenAddress(l.leader_wallet)}</span><div className="dim" style={{ fontSize: 12 }}>{l.total_copies} copies · {l.closed_copies} closed · {l.open_copies} open</div></div>
                    <div style={{ textAlign: 'right' }}><div className={l.total_pnl >= 0 ? 'long-c' : 'short-c'} style={{ fontSize: 18, fontWeight: 700 }}>{l.total_pnl >= 0 ? '+' : ''}{formatUSD(l.total_pnl)}</div><div className="dim" style={{ fontSize: 12 }}>{l.win_rate}% win · {formatUSD(l.total_volume)} vol</div></div>
                  </div>
                </div>
              ))}</div>
        )}

        {pill === 'funding' && <div className="empty">Funding you have paid or received on open positions will be summarised here.</div>}
      </div>
    </div>
  );
}

function Calendar({ series }: { series: DayPoint[] }) {
  // Six-week grid, Mon..Sun. Colour by that day's account-value change.
  const byDate = new Map(series.map((d) => [d.date, d.delta]));
  const today = new Date();
  const start = new Date(today); start.setDate(today.getDate() - 41);
  // align to Monday
  const dow = (start.getDay() + 6) % 7; start.setDate(start.getDate() - dow);
  const maxUp = Math.max(1, ...series.filter((d) => d.delta > 0).map((d) => d.delta));
  const cells: JSX.Element[] = [];
  const heads = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  heads.forEach((h) => cells.push(<div key={'h' + h} className="h">{h}</div>));
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const key = d.toISOString().slice(0, 10);
    const delta = byDate.get(key);
    const future = d > today;
    let cls = ''; let val = '';
    if (delta != null) {
      if (delta < 0) cls = 'neg';
      else if (delta > 0) { const f = delta / maxUp; cls = f > 0.66 ? 'win3' : f > 0.33 ? 'win2' : 'win1'; }
      val = (delta > 0 ? '+' : '') + Math.round(delta);
    }
    cells.push(
      <div key={i} className={`${cls} ${future ? 'fut' : ''}`.trim()}>
        <span>{d.getDate()}</span>{delta != null && <b>{val}</b>}
      </div>
    );
  }
  return <div className="heat">{cells}</div>;
}
