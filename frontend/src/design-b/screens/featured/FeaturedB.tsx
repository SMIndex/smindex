import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist } from '@/hooks/useCopyV1';
import MobileSheetB, { usePhone } from '../../MobileSheetB';
import {
  getFeaturedLive, getFeaturedWallets, getPreview, getWhitelistMe, isWhitelistError, type FeaturedRow, type WalletsAnswer,
} from '@/lib/featuredApi';
import {
  ACTIVE_OPTS, DD_OPTS, FT_DEFAULT, HOLD_OPTS, LEV_OPTS, PF_OPTS, PROFIT_OPTS, SIZE_OPTS, SORT_COLS, TRADES_OPTS, WR_OPTS,
  agoWords, clickFtSort, coinsOnPerpl, ftFiltersActive, ftQuery, hasFtParams, holdShort, parseFt, pct, shortAddr, usd, writeFt,
  type FtSort, type FtView,
} from '@/lib/featuredView';
import { Arrow, LabelChips, PillSelect, VenueBadge } from './bits';
import FeaturedWalletDrawer from './FeaturedWalletDrawer';
import FeaturedTradesTab from './FeaturedTradesTab';
import FeaturedCopySheet from './FeaturedCopySheet';
import WhitelistGate from './WhitelistGate';
import './featured.css';

// Featured traders (docs/featured-traders): Tab 1 Winning Wallets
// (design/Main.dc.html, Mobile.dc.html), Tab 2 Winning Trades, the wallet
// drawer, the copy sheet and the whitelist gate. Data only from /api/featured.

const VIEW_KEY = 'smindex.featured.view';
const SORT_LABEL: Record<FtSort, string> = {
  score: 'Score', win_rate_90d: 'Win rate', net_pnl_90d: 'Net profit 90 d', profit_factor_90d: 'Profit factor', avg_win_usd: 'Avg win',
  max_drawdown_90d: 'Max DD', trades_per_week: 'Trades', open_count: 'Open now', last_trade_ms: 'Last trade', trades_90d: 'Trade count',
  account_value: 'Account size', median_leverage: 'Leverage',
};
const SIZE_LABEL: Record<string, string> = { '': 'Any', lt10k: 'under $10K', '10k-100k': '$10K to $100K', '100k-1m': '$100K to $1M', gt1m: 'over $1M' };
const HOLD_LABEL: Record<string, string> = { '': 'Any', scalper: 'Scalper (under 1 h)', intraday: 'Intraday (1 to 24 h)', swing: 'Swing (over 1 day)' };
const ACTIVE_LABEL: Record<string, string> = { '24h': '24 h', '7d': '7 d', '14d': '14 d', any: 'Any' };

function Th({ col, label, view, onSort, title }: { col: FtSort; label: string; view: FtView; onSort: (c: FtSort) => void; title?: string }) {
  const on = view.sort === col;
  return (
    <th title={title}><button aria-pressed={on} onClick={() => onSort(col)}>{label}{on ? (view.dir === 'desc' ? ' ↓' : ' ↑') : ''}</button></th>
  );
}

function fmtPf(v: number | null): string {
  if (v == null) return 'n/a';
  return v >= 999 ? 'no losses' : v.toFixed(1);
}

export default function FeaturedB() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isAuthenticated, address } = useAuth();
  const phone = usePhone();
  const watchlist = useWatchlist(isAuthenticated);
  const [sp, setSp] = useSearchParams();
  const tab = location.pathname.endsWith('/trades') ? 'trades' : 'wallets';
  const view = useMemo(() => parseFt(sp), [sp]);
  const setView = (v: FtView) => setSp((p) => writeFt(p, v), { replace: true });
  useEffect(() => {
    if (hasFtParams(sp)) return;
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved) setSp((p) => writeFt(p, parseFt(new URLSearchParams(saved))), { replace: true });
    } catch { /* storage blocked */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    try { localStorage.setItem(VIEW_KEY, writeFt(new URLSearchParams(), view).toString()); } catch { /* ignore */ }
  }, [view]);

  // gate
  const [gate, setGate] = useState<'checking' | 'open' | 'closed'>('checking');
  const [admin, setAdmin] = useState(false);
  const [preview, setPreview] = useState<any>(null);
  const checkGate = useCallback(() => {
    getWhitelistMe().then((m) => { setAdmin(m.admin); setGate(m.whitelisted ? 'open' : 'closed'); }).catch(() => setGate('closed'));
  }, []);
  useEffect(() => { checkGate(); }, [checkGate, isAuthenticated, address]);
  useEffect(() => { if (gate === 'closed' && !preview) getPreview().then(setPreview).catch(() => {}); }, [gate, preview]);

  // rows
  const [data, setData] = useState<WalletsAnswer | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState<Record<string, { last_trade_at: string | null; open_count: number; unrealized_usd: number | null }>>({});
  const seq = useRef(0);
  const load = useCallback((quiet = false) => {
    if (gate !== 'open') return;
    const n = ++seq.current;
    if (!quiet) setLoading(true);
    getFeaturedWallets(ftQuery(view)).then((d) => { if (seq.current === n) { setData(d); setErr(null); } })
      .catch((e) => {
        if (seq.current !== n) return;
        if (isWhitelistError(e)) setGate('closed');
        else setErr(e?.response?.data?.detail || 'Featured wallets are unavailable right now');
      })
      .finally(() => { if (seq.current === n) setLoading(false); });
  }, [gate, view]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (gate !== 'open') return;
    const t = setInterval(() => load(true), 60_000);
    return () => clearInterval(t);
  }, [gate, load]);
  const wallets = useMemo(() => (data?.rows ?? []).map((r) => r.wallet), [data]);
  useEffect(() => {
    if (gate !== 'open' || !wallets.length) return;
    const tick = () => getFeaturedLive(wallets).then((r) => setLive(r.rows)).catch(() => {});
    tick();
    const t = setInterval(tick, 15_000);
    return () => clearInterval(t);
  }, [gate, wallets]);

  const rows: FeaturedRow[] = useMemo(() => (data?.rows ?? []).map((r) => {
    const l = live[`${r.venue}:${r.wallet}`];
    if (!l) return r;
    const lt = l.last_trade_at && (!r.last_trade_at || l.last_trade_at > r.last_trade_at) ? l.last_trade_at : r.last_trade_at;
    return { ...r, last_trade_at: lt, open_count: l.open_count, unrealized_usd: l.unrealized_usd };
  }), [data, live]);
  const rowsByKey = useMemo(() => new Map(rows.map((r) => [`${r.venue}:${r.wallet}`, r])), [rows]);

  const [drawer, setDrawer] = useState<FeaturedRow | null>(null);
  const [copyRow, setCopyRow] = useState<FeaturedRow | null>(null);
  const onSort = (c: FtSort) => setView(clickFtSort(view, c));
  const nActive = ftFiltersActive(view);
  const openWallet = (venue: string, wallet: string) => {
    const r = rowsByKey.get(`${venue}:${wallet}`);
    if (r) setDrawer(r);
    else setDrawer({ venue: venue as 'hl' | 'perpl', wallet, display_name: null, label: null, qualified: true, inactive: false, one_big_win: false,
      score: null, verdict: null, style: null, confidence: null, trades_90d: 0, days_active_90d: 0, win_rate_90d: null, win_rate_30d: null,
      net_pnl_90d: null, net_pnl_30d: null, net_pnl_all: null, profit_factor_90d: null, profit_factor_30d: null, avg_win_usd: null, avg_loss_usd: null,
      avg_win_pct_eq: null, avg_loss_pct_eq: null, avg_win_move_pct: null, avg_loss_move_pct: null, max_drawdown_90d: null, dd_start: null, dd_end: null,
      worst_trade_usd: null, worst_trade_pct_eq: null, worst_day_usd: null, worst_week_usd: null, largest_trade_share: null, profitable_weeks_13: null,
      longest_loss_streak: null, loss_streak_at: null, median_hold_sec: null, trades_per_week: null, trades_per_day_max: null, median_leverage: null,
      max_leverage: null, maker_share: null, pnl_per_volume: null, long_share: null, avg_equity_90d: null, account_value: null, volume_90d: null,
      top_coins: [], top_coins_on_perpl: 0, copiable_pct: null, liquidations_90d: 0, usual_open: null, history_from: null, last_trade_at: null,
      last_trade_ms: null, open_count: 0, unrealized_usd: null, positions_at: null, streak_now: null, averages_down_n: null, adds_to_winner_n: null,
      momentum: { win_rate: null, pf: null, pnl: null }, checks_passed: 0, active: true, rank: 0, as_of: null });
  };
  const onWatch = async (r: FeaturedRow) => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(r.wallet, r.venue)) await watchlist.unwatch(r.wallet, r.venue); else await watchlist.watch(r.wallet, r.venue);
  };
  const coinOptions = useMemo(() => Array.from(new Set(rows.flatMap((r) => r.top_coins))).sort(), [rows]);
  const liveNewest = useMemo(() => Object.values(live).map((l) => l.last_trade_at).filter(Boolean).sort().pop() ?? null, [live]);

  const header = (
    <>
      <div className="ft-head">
        <div>
          <h1>Featured traders</h1>
          <div className="sub" style={{ marginTop: 4 }}>
            {tab === 'wallets'
              ? 'Wallets that make money by trading, on Hyperliquid and Perpl. Past 90 days. Past results do not predict future results.'
              : 'What the qualified wallets are doing right now, and every trade they closed. Past results do not predict future results.'}
          </div>
        </div>
        {gate === 'open' && <div className="sub">Metrics as of {data?.as_of ?? '…'} 02:00 UTC · live fields <span className="live-dot" /> {liveNewest ? agoWords(liveNewest) : 'waiting for a trade'}</div>}
      </div>
      <div className="ft-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'wallets'} onClick={() => navigate({ pathname: '/copy/featured/wallets', search: location.search })}>Winning Wallets {data && <span className="chip chip-grey" style={{ marginLeft: 6 }}>{data.count}</span>}</button>
        <button role="tab" aria-selected={tab === 'trades'} onClick={() => navigate({ pathname: '/copy/featured/trades', search: location.search })}>Winning Trades <span className="chip chip-grey" style={{ marginLeft: 6 }}>live</span></button>
        {admin && <button role="tab" aria-selected={false} onClick={() => navigate('/admin/featured-whitelist')} style={{ marginLeft: 'auto' }}>Whitelist admin</button>}
      </div>
    </>
  );

  if (gate === 'checking') return <div className="screen ft">{header}<div className="ft-empty">Checking access…</div></div>;

  if (gate === 'closed') {
    const sample: FeaturedRow[] = preview?.rows ?? [];
    return (
      <div className="screen ft">
        <div className="ft-gate">
          <div className="faded" aria-hidden="true">
            {header}
            <div className="ft-filters">{['Venue', 'Label', 'Min win rate', 'Min profit factor', 'Min trades', 'Max drawdown', 'Active within'].map((l) => <span className="pill" key={l}><b>{l}</b><span className="v">Any ▾</span></span>)}</div>
            <div className="ft-table-wrap">
              <table className="ft-table">
                <thead><tr><th>#</th><th>Trader</th><th>Win rate</th><th>Net profit 90 d</th><th>Profit factor</th><th>Avg win / loss</th><th>Max DD</th><th>Trades · lev</th><th>Coins</th><th>Open now</th><th>Last trade</th><th>Score</th></tr></thead>
                <tbody>
                  {(sample.length ? sample : Array.from({ length: 6 }, (_, i) => ({ rank: i + 1 } as FeaturedRow))).map((r, i) => (
                    <tr key={i}>
                      <td className="sub">{r.rank ?? i + 1}</td>
                      <td><div className="trader"><span className="venue hl">H</span><div><div className="nm">0x0000…0000</div><span className="chip chip-steady">Sample</span></div></div></td>
                      <td>{r.win_rate_90d != null ? pct(r.win_rate_90d * 100) : '00%'}</td><td className="pos">{r.net_pnl_90d != null ? usd(r.net_pnl_90d, true) : '+$0'}</td>
                      <td>{r.profit_factor_90d ?? '0.0'}</td><td>{usd(r.avg_win_usd ?? 0)} / {usd(r.avg_loss_usd ?? 0)}</td><td>{pct(r.max_drawdown_90d ?? 0)}</td>
                      <td>{r.trades_per_week ?? 0}/wk</td><td>{(r.top_coins ?? []).join(' · ') || 'BTC · ETH'}</td><td>{r.open_count ?? 0}</td><td>sample</td><td>{r.score ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="sub">Sample layout. Real wallets, numbers and trades show once your wallet is whitelisted.</div>
          </div>
          <WhitelistGate onRequested={checkGate} />
        </div>
      </div>
    );
  }

  return (
    <div className="screen ft">
      {header}
      {tab === 'trades' ? (
        <FeaturedTradesTab onCopyWallet={(r) => setCopyRow(r)} onOpenWallet={openWallet} rowsByKey={rowsByKey} />
      ) : (
        <>
          {phone ? (
            <div className="mbar">
              <MobileSheetB label={`Filters${nActive ? ` · ${nActive}` : ''}`} title="Filter wallets">
                <Filters view={view} setView={setView} coinOptions={coinOptions} phone />
              </MobileSheetB>
              <span className="pill" style={{ height: 36 }}><b>Sort: {SORT_LABEL[view.sort]} {view.dir === 'desc' ? '▾' : '▴'}</b>
                <select aria-label="Sort" value={view.sort} onChange={(e) => setView({ ...view, sort: e.target.value as FtSort })}>{SORT_COLS.map((c) => <option key={c} value={c}>{SORT_LABEL[c]}</option>)}</select>
              </span>
              <span className="sub" style={{ marginLeft: 'auto' }}>{data ? `${data.count} wallets` : ''}</span>
            </div>
          ) : (
            <div className="ft-filters">
              <Filters view={view} setView={setView} coinOptions={coinOptions} />
              <span className="sub ft-count">
                <label><input type="checkbox" checked={view.hideSmall} onChange={(e) => setView({ ...view, hideSmall: e.target.checked })} /> Hide small accounts (under $500 profit)</label>
                {data ? `${data.count} of ${data.total} scored wallets match · sorted by ${SORT_LABEL[view.sort].toLowerCase()}` : ''}
              </span>
            </div>
          )}

          {err && <div className="ft-empty" style={{ color: 'var(--short)' }}>{err}</div>}
          {loading && !data ? <div className="ft-empty">Loading wallets…</div> : rows.length === 0 ? (
            <div className="ft-empty">
              {data && data.total === 0 ? 'No wallet has passed every rule yet. The daily run at 02:00 UTC writes the first set.' : 'No wallet matches these filters.'}
              {nActive > 0 && <div style={{ marginTop: 10 }}><button className="btn primary" onClick={() => setView(FT_DEFAULT)}>Reset filters</button></div>}
            </div>
          ) : phone ? (
            <div className="mlist">
              {rows.map((r) => (
                <div className="mcard" key={`${r.venue}:${r.wallet}`}>
                  <div className="top"><VenueBadge venue={r.venue} /><span className="mono" style={{ fontWeight: 600, fontSize: 13 }}>{shortAddr(r.wallet)}</span><LabelChips label={r.label} oneBigWin={r.one_big_win} inactive={r.inactive} /><span className="sub" style={{ marginLeft: 'auto' }}>Score {r.score != null ? Math.round(r.score) : 'n/a'}</span></div>
                  <div className="verdict">{r.verdict}</div>
                  <div className="st">
                    <div><span className="sub">Win rate</span><b>{pct((r.win_rate_90d ?? 0) * 100)}</b><span className="sub">of {r.trades_90d}</span></div>
                    <div><span className="sub">Profit 90 d</span><b className={(r.net_pnl_90d ?? 0) >= 0 ? 'pos' : 'neg'}>{usd(r.net_pnl_90d, true, true)}</b><span className="sub">30 d <Arrow dir={r.momentum?.pnl} /></span></div>
                    <div><span className="sub">P. factor</span><b>{fmtPf(r.profit_factor_90d)}</b></div>
                    <div><span className="sub">Max DD</span><b>{pct(r.max_drawdown_90d)}</b></div>
                  </div>
                  <div className="sub" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}><span>{r.open_count} open{r.unrealized_usd != null ? `, ${usd(r.unrealized_usd, true, true)}` : ''}</span><span>last trade {agoWords(r.last_trade_at)}</span></div>
                  <div className="acts" style={{ display: 'flex', gap: 8 }}>
                    <button className="btn primary" onClick={() => setCopyRow(r)}>Copy</button>
                    <button className="btn" onClick={() => onWatch(r)} disabled={!isAuthenticated}>{watchlist.isWatched(r.wallet, r.venue) ? 'Watching' : 'Watch'}</button>
                    <button className="btn" onClick={() => setDrawer(r)}>Details</button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="ft-table-wrap">
              <table className="ft-table">
                <thead>
                  <tr>
                    <th>#</th><th>Trader</th>
                    <Th col="win_rate_90d" label="Win rate" view={view} onSort={onSort} title="Won round trips over all round trips, last 90 days" />
                    <Th col="net_pnl_90d" label="Net profit 90 d" view={view} onSort={onSort} title="Realized profit after fees and funding" />
                    <Th col="profit_factor_90d" label="Profit factor" view={view} onSort={onSort} title="Gross wins over gross losses" />
                    <Th col="avg_win_usd" label="Avg win / loss" view={view} onSort={onSort} />
                    <Th col="max_drawdown_90d" label="Max DD" view={view} onSort={onSort} title="Peak to trough of the 90-day realized curve" />
                    <Th col="trades_per_week" label="Trades · lev" view={view} onSort={onSort} />
                    <th>Coins</th>
                    <Th col="open_count" label="Open now" view={view} onSort={onSort} />
                    <Th col="last_trade_ms" label="Last trade · streak" view={view} onSort={onSort} />
                    <Th col="score" label="Score" view={view} onSort={onSort} title="0 to 100 for ordering: 30% profit factor, 20% win rate, 15% consistency, 15% drawdown, 10% sample, 10% copy fit" />
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.venue}:${r.wallet}`} onClick={() => setDrawer(r)}>
                      <td className="sub">{r.rank}</td>
                      <td><div className="trader"><VenueBadge venue={r.venue} /><div><div className="nm">{shortAddr(r.wallet)}{r.display_name && <span className="sub"> {r.display_name}</span>}</div><div style={{ marginTop: 3, display: 'flex', gap: 4 }}><LabelChips label={r.label} oneBigWin={r.one_big_win} inactive={r.inactive} history={r.history_from} /></div></div></div></td>
                      <td><div style={{ fontWeight: 700 }}>{pct((r.win_rate_90d ?? 0) * 100)}</div><div className="sub">of {r.trades_90d}</div></td>
                      <td><div className={(r.net_pnl_90d ?? 0) >= 0 ? 'pos' : 'neg'}>{usd(r.net_pnl_90d, true)}</div><div className="sub" title={`All time ${usd(r.net_pnl_all, true)}`}>30 d {usd(r.net_pnl_30d, true, true)} <Arrow dir={r.momentum?.pnl} /></div></td>
                      <td style={{ fontWeight: 600 }}>{fmtPf(r.profit_factor_90d)} <Arrow dir={r.momentum?.pf} /></td>
                      <td><span className="pos">{usd(r.avg_win_usd)}</span> / <span className="neg">{usd(r.avg_loss_usd)}</span><div className="sub">{r.avg_win_pct_eq != null ? `${pct(r.avg_win_pct_eq, 1)} / ${pct(r.avg_loss_pct_eq, 1)} eq.` : 'equity n/a'}</div></td>
                      <td>{pct(r.max_drawdown_90d)}</td>
                      <td>{r.trades_per_week?.toFixed(1)}/wk{r.median_leverage ? ` · ${r.median_leverage}x` : ''}<div className="sub">hold {holdShort(r.median_hold_sec)}</div></td>
                      <td>{r.top_coins.join(' · ') || 'n/a'}<div className="sub">{coinsOnPerpl(r.top_coins, r.top_coins_on_perpl)}</div></td>
                      <td>{r.open_count}{r.unrealized_usd != null && r.open_count > 0 ? <> · <span className={r.unrealized_usd >= 0 ? 'pos' : 'neg'}>{usd(r.unrealized_usd, true)}</span></> : null}</td>
                      <td>{agoWords(r.last_trade_at)}<div className="sub">{r.streak_now ? <><span className={r.streak_now.startsWith('W') ? 'pos' : 'neg'}>{r.streak_now}</span> streak</> : ''}</div></td>
                      <td><div style={{ fontWeight: 700 }}>{r.score != null ? Math.round(r.score) : 'n/a'}</div><div className="bar"><i style={{ width: `${r.score ?? 0}%` }} /></div></td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="acts">
                          <button className="btn primary" onClick={() => setCopyRow(r)}>Copy</button>
                          <button className="btn" aria-pressed={watchlist.isWatched(r.wallet, r.venue)} disabled={!isAuthenticated} title={isAuthenticated ? '' : 'Sign in to watch'} onClick={() => onWatch(r)}>{watchlist.isWatched(r.wallet, r.venue) ? 'Watching' : 'Watch'}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="ft-explain">
            <div className="ft-card"><div className="lab">How a wallet gets here</div><p>At least 30 closed trades over 30 days, net profit after fees, no single loss over 20% of equity, drawdown under 30%, no liquidations, and it is not a market maker, volume farmer, vault, bot or hedger. Every check is shown on the wallet.</p></div>
            <div className="ft-card"><div className="lab">Labels</div><p><span className="chip chip-steady">Steady</span> wins 70% or more of its trades, profit factor over 1.5. <span className="chip chip-big">Big winners</span> wins less often, but the average win is at least twice the average loss. <span className="chip chip-warn">One big win</span> means over half the profit came from one trade.</p></div>
            <div className="ft-card"><div className="lab">What Copy does</div><p>Same copy flow as Discover: your size, your leverage, a stop loss on every copy, you confirm each order on Perpl. The sheet shows what the last 90 days would have returned at your settings before you commit.</p></div>
          </div>
        </>
      )}

      {drawer && <FeaturedWalletDrawer row={drawer} onClose={() => setDrawer(null)} liveAt={live[`${drawer.venue}:${drawer.wallet}`]?.last_trade_at} />}
      {copyRow && <FeaturedCopySheet row={copyRow} defaults={null} replay={null} onClose={() => setCopyRow(null)} />}
    </div>
  );
}

function Filters({ view, setView, coinOptions, phone }: { view: FtView; setView: (v: FtView) => void; coinOptions: string[]; phone?: boolean }) {
  const [coinsOpen, setCoinsOpen] = useState(false);
  const toggleCoin = (c: string) => setView({ ...view, coins: view.coins.includes(c) ? view.coins.filter((x) => x !== c) : [...view.coins, c] });
  return (
    <>
      <PillSelect label="Venue" value={view.venue} opts={['all', 'hl', 'perpl']} fmt={(v) => (v === 'all' ? 'All' : v === 'hl' ? 'Hyperliquid' : 'Perpl')} onChange={(v) => setView({ ...view, venue: v })} />
      <PillSelect label="Label" value={view.label} opts={['both', 'consistent', 'asymmetric']} fmt={(v) => (v === 'both' ? 'Steady, Big winners' : v === 'consistent' ? 'Steady' : 'Big winners')} onChange={(v) => setView({ ...view, label: v })} />
      <PillSelect label="Min win rate" value={view.minWinRate} opts={WR_OPTS} fmt={(v) => (v ? `${v}%` : 'Any')} onChange={(v) => setView({ ...view, minWinRate: v })} />
      <PillSelect label="Min profit factor" value={view.minPf} opts={PF_OPTS} fmt={(v) => (v ? String(v) : 'Any')} onChange={(v) => setView({ ...view, minPf: v })} />
      <PillSelect label="Min trades" value={view.minTrades} opts={TRADES_OPTS} fmt={(v) => String(v)} onChange={(v) => setView({ ...view, minTrades: v })} />
      <PillSelect label="Min net profit" value={view.minProfit} opts={PROFIT_OPTS} fmt={(v) => (v ? usd(v, false, true) : '$500')} onChange={(v) => setView({ ...view, minProfit: v })} />
      <PillSelect label="Max drawdown" value={view.maxDd} opts={DD_OPTS} fmt={(v) => (v ? `${v}%` : 'Any')} onChange={(v) => setView({ ...view, maxDd: v })} />
      <PillSelect label="Max leverage" value={view.maxLev} opts={LEV_OPTS} fmt={(v) => (v ? `${v}x` : 'Any')} onChange={(v) => setView({ ...view, maxLev: v })} />
      <PillSelect label="Account size" value={view.size} opts={SIZE_OPTS} fmt={(v) => SIZE_LABEL[v]} onChange={(v) => setView({ ...view, size: v })} />
      <PillSelect label="Hold" value={view.hold} opts={HOLD_OPTS} fmt={(v) => HOLD_LABEL[v]} onChange={(v) => setView({ ...view, hold: v })} />
      <button className="pill" aria-pressed={view.coins.length > 0} onClick={() => setCoinsOpen((o) => !o)}><b>Coins</b><span className="v">{view.coins.length ? view.coins.join(', ') : 'Any'} ▾</span></button>
      <button className="pill" aria-pressed={view.perplOnly} onClick={() => setView({ ...view, perplOnly: !view.perplOnly })}><b>Perpl-listed only</b></button>
      <PillSelect label="Active within" value={view.active} opts={ACTIVE_OPTS} fmt={(v) => ACTIVE_LABEL[v]} onChange={(v) => setView({ ...view, active: v })} />
      <button className="btn" onClick={() => setView(FT_DEFAULT)} disabled={ftFiltersActive(view) === 0 && view.sort === 'score' && view.dir === 'desc'}>Reset</button>
      {phone && <label className="sub" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={view.hideSmall} onChange={(e) => setView({ ...view, hideSmall: e.target.checked })} /> Hide small accounts (under $500 profit)</label>}
      {(coinsOpen || phone) && (
        <div className="coinbox" style={{ width: '100%' }}>
          {coinOptions.length === 0 ? <span className="sub">Coins appear once wallets are listed</span> : coinOptions.map((c) => <button key={c} aria-pressed={view.coins.includes(c)} onClick={() => toggleCoin(c)}>{c}</button>)}
          {view.coins.length > 0 && <button onClick={() => setView({ ...view, coins: [] })}>clear</button>}
        </div>
      )}
    </>
  );
}
