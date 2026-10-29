import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist } from '@/hooks/useCopyV1';
import { useWebSocket } from '@/hooks/useWebSocket';
import MobileSheetB, { usePhone } from '../../MobileSheetB';
import { getFeaturedTrades, getOpenNow, getTradeDetail, type FeaturedRow, type OpenNowCoin, type TradeDetail, type TradeRow } from '@/lib/featuredApi';
import { agoWords, holdWords, price, shortAddr, usd, pct } from '@/lib/featuredView';
import { Drawer, LabelChips, LiveDot, PillSelect, VenueBadge } from './bits';

// Tab 2, Winning Trades (design/WinningTrades.dc.html): Open now by coin, the
// closed-trade feed (newest first, websocket slide-in), trade detail drawer.

const RESULT = ['all', 'wins', 'losses'] as const;
const WITHIN = ['1h', '24h', '7d', '30d'] as const;
const HOLD = ['', 'scalper', 'intraday', 'swing'] as const;
const MINPNL = [0, 100, 1000, 10000];
const MINPCT = [0, 5, 20, 50];

function entryStyle(t: TradeRow): [string, string] {
  const first = t.entry_taker == null ? 'Entry' : t.entry_taker ? 'Taker' : 'Maker';
  let second = 'single entry';
  if (t.averaged_down) second = 'averaged down';
  else if (t.added_to_winner) second = 'added to winner';
  else if (t.n_closes > 1) second = 'scaled out';
  else if (t.pnl_usd < 0 && t.hold_sec < 3600) second = 'cut fast';
  return [first, second];
}

function contextWords(t: TradeRow): [string, string] {
  const r = t.entry_vs_24h_range;
  let a = 'Context not computed yet';
  if (r != null) {
    if (t.side === 'long') a = r < 0.25 ? 'Bought near 24 h low' : r > 0.75 ? 'Breakout entry' : 'Mid range';
    else a = r > 0.75 ? 'Sold at 24 h high' : r > 0.5 ? 'Sold into strength' : 'Mid range';
  }
  return [a, t.smi_at_entry != null ? `SMI ${Math.round(t.smi_at_entry)} at entry` : 'SMI n/a'];
}

function fmtUtc(iso: string): string {
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
}

function PriceChart({ d }: { d: TradeDetail }) {
  const cs = d.candles;
  const W = 600, H = 170, pad = 10;
  if (!cs.length) return <div className="ft-empty" style={{ marginTop: 8 }}>No candle data for this window</div>;
  const t0 = cs[0].t, t1 = cs[cs.length - 1].t || t0 + 1;
  const lo = Math.min(...cs.map((c) => c.l)), hi = Math.max(...cs.map((c) => c.h));
  const x = (t: number) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const y = (v: number) => H - 30 - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - 50);
  const fills = d.fills.map((f) => ({ ...f, ms: Date.parse(f.time.endsWith('Z') ? f.time : f.time + 'Z') }));
  const bw = Math.max(1, (W - 2 * pad) / cs.length - 1);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 170, marginTop: 8 }} role="img" aria-label={`${d.coin} price over the trade window with entry and exit fills marked`}>
      {cs.map((c, i) => (
        <g key={i}>
          <line x1={x(c.t) + bw / 2} x2={x(c.t) + bw / 2} y1={y(c.h)} y2={y(c.l)} stroke="var(--dim)" strokeWidth="1" />
          <rect x={x(c.t)} y={y(Math.max(c.o, c.c))} width={bw} height={Math.max(1, Math.abs(y(c.o) - y(c.c)))} fill={c.c >= c.o ? 'var(--long)' : 'var(--short)'} opacity=".8" />
        </g>
      ))}
      {fills.map((f, i) => {
        const opening = (f.side === 'B') === (d.side === 'long');
        return <circle key={i} cx={x(f.ms)} cy={y(f.px)} r="5" fill={opening ? 'var(--long)' : 'var(--short)'} stroke="var(--s1)" strokeWidth="1.5"><title>{`${fmtUtc(f.time)} ${opening ? (i === 0 ? 'entry' : 'add') : 'close'} ${f.sz} at ${price(f.px)}`}</title></circle>;
      })}
      <text x={pad} y={H - 8} fontSize="10">{fmtUtc(d.open_time)} UTC</text>
      <text x={W - pad} y={H - 8} fontSize="10" textAnchor="end">{fmtUtc(d.close_time)} UTC</text>
      <text x={pad} y={12} fontSize="10">{price(hi)}</text>
      <text x={pad} y={H - 32} fontSize="10">{price(lo)}</text>
    </svg>
  );
}

function TradeDetailDrawer({ id, onClose, onCopy, onWallet }: { id: number; onClose: () => void; onCopy: (t: TradeRow) => void; onWallet: (t: TradeRow) => void }) {
  const [d, setD] = useState<TradeDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    setD(null);
    getTradeDetail(id).then((x) => on && setD(x)).catch((e) => on && setErr(e?.response?.data?.detail || 'Could not load this trade'));
    return () => { on = false; };
  }, [id]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  return (
    <Drawer onClose={onClose} label="Trade detail">
      <div className="dhead">
        <div className="who">
          <div className="lab">Trade detail{d ? `: ${d.coin} ${d.side} by ${shortAddr(d.wallet)}` : ''}</div>
          {d && <div className="verdict"><span className={d.pnl_usd >= 0 ? 'pos' : 'neg'}>{usd(d.pnl_usd, true)}</span> {d.pnl_pct_margin != null ? `(${pct(d.pnl_pct_margin, 1)} of margin)` : `(${pct(d.ret_pct, 2)} price move)`} · held {holdWords(d.hold_sec)} · size {usd(d.max_size_usd)}{d.leverage ? ` at ${d.leverage}x` : ''}{d.on_perpl ? ' · on Perpl' : ' · not on Perpl'}</div>}
        </div>
        <div className="dacts">
          {d && <button className="btn" onClick={() => onWallet(d)}>Wallet</button>}
          {d && <button className="btn primary" onClick={() => onCopy(d)}>Copy wallet</button>}
          <button className="btn" aria-label="Close" onClick={onClose}>✕</button>
        </div>
      </div>
      {err && <div className="ft-empty" style={{ color: 'var(--short)' }}>{err}</div>}
      {d && (
        <div className="ft-card tdetail">
          <div>
            <div className="lab">{d.coin} price over the window · entry and exit fills marked</div>
            <PriceChart d={d} />
            <div className="sub">{d.venue === 'hl' ? 'Hyperliquid candles' : 'Perpl candles'}{d.mae_pct != null ? ` · worst move against the trade ${d.mae_pct.toFixed(2)}%${d.mae_src === 'candles' ? ' (candles)' : ' (fills only)'}` : ''}</div>
          </div>
          <div style={{ fontSize: 13 }}>
            <div className="lab">Fills</div>
            {d.fills.length ? d.fills.map((f, i) => {
              const opening = (f.side === 'B') === (d.side === 'long');
              return <div className="frow" key={i}><span>{fmtUtc(f.time)} {opening ? (i === 0 ? `Open ${d.side}` : 'Add') : 'Close'} {f.sz} {d.coin}{f.crossed === false ? ' (maker)' : ''}</span><span className="mono">{price(f.px)}</span></div>;
            }) : <div className="sub">Fill rows are kept for Hyperliquid trades only</div>}
            <div className="frow"><span className="sub">Fees {usd(d.fees)} · funding {d.funding >= 0 ? 'received' : 'paid'} {usd(Math.abs(d.funding))}{d.other_positions.length ? ` · other open positions at the time: ${d.other_positions.map((o) => `${o.coin} ${o.side}`).join(', ')}` : ' · no other position open at the time'}</span></div>
            {!!d.funding_rows.length && <div className="sub" style={{ marginTop: 6 }}>{d.funding_rows.length} funding settlement{d.funding_rows.length === 1 ? '' : 's'} during the hold</div>}
          </div>
        </div>
      )}
    </Drawer>
  );
}

export default function FeaturedTradesTab({ onCopyWallet, onOpenWallet, rowsByKey }: {
  onCopyWallet: (row: FeaturedRow) => void; onOpenWallet: (venue: string, wallet: string) => void; rowsByKey: Map<string, FeaturedRow>;
}) {
  const { isAuthenticated } = useAuth();
  const watchlist = useWatchlist(isAuthenticated);
  const { subscribe } = useWebSocket();
  const phone = usePhone();
  const [sp, setSp] = useSearchParams();
  const venue = (sp.get('tvenue') || 'all');
  const coin = sp.get('tcoin') || '';
  const side = sp.get('tside') || '';
  const result = (RESULT as readonly string[]).includes(sp.get('result') || '') ? (sp.get('result') as string) : 'all';
  const minPnl = Number(sp.get('minPnl') || 0);
  const minPct = Number(sp.get('minPnlPct') || 0);
  const hold = sp.get('thold') || '';
  const within = (WITHIN as readonly string[]).includes(sp.get('within') || '') ? (sp.get('within') as string) : '24h';
  const label = sp.get('tlabel') || '';
  const watched = sp.get('watched') === '1';
  const set = (k: string, v: string | number | boolean, dflt: string | number | boolean) =>
    setSp((p) => { const o = new URLSearchParams(p); if (v === dflt || v === '' || v === 0 || v === false) o.delete(k); else o.set(k, String(v === true ? 1 : v)); return o; }, { replace: true });
  const reset = () => setSp((p) => { const o = new URLSearchParams(p); ['tvenue', 'tcoin', 'tside', 'result', 'minPnl', 'minPnlPct', 'thold', 'within', 'tlabel', 'watched'].forEach((k) => o.delete(k)); return o; }, { replace: true });

  const [open, setOpen] = useState<{ coins: OpenNowCoin[]; wallets: number; positions: number; as_of: string | null } | null>(null);
  const [showAllCoins, setShowAllCoins] = useState(false);
  const [seeWallets, setSeeWallets] = useState<string | null>(null);
  const [rows, setRows] = useState<TradeRow[]>([]);
  const [meta, setMeta] = useState<{ count: number; wins: number; next: number | null }>({ count: 0, wins: 0, next: null });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [detail, setDetail] = useState<number | null>(null);
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const paramsRef = useRef<Record<string, string | number>>({});

  const params = useMemo(() => ({ venue, coin, side, result, minPnl, minPnlPct: minPct, hold, within, label, watched: watched ? 1 : 0, limit: 50 }),
    [venue, coin, side, result, minPnl, minPct, hold, within, label, watched]);
  paramsRef.current = params;

  const load = useCallback((cursor?: number | null) => {
    if (!cursor) setLoading(true);
    getFeaturedTrades({ ...paramsRef.current, ...(cursor ? { cursor } : {}) }).then((r) => {
      setRows((cur) => (cursor ? [...cur, ...r.rows] : r.rows));
      setMeta({ count: r.count_window, wins: r.wins_window, next: r.next_cursor });
      setErr(null);
    }).catch((e) => setErr(e?.response?.data?.detail || 'Trades are unavailable right now')).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [params, load]);
  useEffect(() => {
    const loadOpen = () => getOpenNow().then(setOpen).catch(() => {});
    loadOpen();
    const t = setInterval(loadOpen, 15_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    subscribe('featured.trades');
    const h = (e: Event) => {
      const t = (e as CustomEvent).detail?.trade as TradeRow | undefined;
      if (!t) return;
      const p = paramsRef.current;
      if (p.venue !== 'all' && t.venue !== p.venue) return;
      if (p.result === 'wins' && t.pnl_usd <= 0) return;
      if (p.result === 'losses' && t.pnl_usd >= 0) return;
      if (p.coin && !String(p.coin).split(',').includes(t.coin)) return;
      if (p.side && t.side !== p.side) return;
      if (p.label && t.label !== p.label) return;
      setRows((cur) => (cur.some((x) => x.id === t.id) ? cur : [t, ...cur]));
      setMeta((m) => ({ ...m, count: m.count + 1, wins: m.wins + (t.pnl_usd > 0 ? 1 : 0) }));
      setFresh((s) => new Set(s).add(t.id));
      setTimeout(() => setFresh((s) => { const n = new Set(s); n.delete(t.id); return n; }), 1500);
      getOpenNow().then(setOpen).catch(() => {});
    };
    window.addEventListener('featured_trade', h);
    return () => window.removeEventListener('featured_trade', h);
  }, [subscribe]);

  const coins = useMemo(() => Array.from(new Set([...(open?.coins.map((c) => c.coin) ?? []), ...rows.map((r) => r.coin)])).sort(), [open, rows]);
  const rowFor = (t: TradeRow): FeaturedRow | null => rowsByKey.get(`${t.venue}:${t.wallet}`) ?? null;
  const copy = (t: TradeRow) => {
    const r = rowFor(t);
    if (r) onCopyWallet(r);
    else onOpenWallet(t.venue, t.wallet);
  };
  const onWatch = async (t: TradeRow) => {
    if (!isAuthenticated) return;
    if (watchlist.isWatched(t.wallet, t.venue)) await watchlist.unwatch(t.wallet, t.venue); else await watchlist.watch(t.wallet, t.venue);
  };
  const shownCoins = showAllCoins ? (open?.coins ?? []) : (open?.coins ?? []).slice(0, 4);
  const withinWords: Record<string, string> = { '1h': '1 h', '24h': '24 h', '7d': '7 d', '30d': '30 d' };

  return (
    <>
      <div className="ft-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div><div className="lab">Open now · {open?.wallets ?? 0} featured wallets · {open?.positions ?? 0} positions</div><div className="sub" style={{ marginTop: 2 }}>Grouped by coin. Long vs short count and total size. Updates within seconds of a fill.</div></div>
          <LiveDot at={open?.as_of} maxSec={600} />
        </div>
        {open && open.coins.length === 0 && <div className="sub" style={{ marginTop: 10 }}>No featured wallet holds a position right now.</div>}
        <div className="onow">
          {shownCoins.map((c) => {
            const tot = c.long + c.short || 1;
            const lean = c.smi == null ? '' : c.smi >= 60 ? 'leaning long' : c.smi <= 40 ? 'leaning short' : 'neutral';
            return (
              <div className="co" key={c.coin}>
                <div className="t">{c.coin} <span className="sub" style={{ fontWeight: 400 }}>{usd(c.size_usd, false, true)}</span></div>
                <div className="ls"><i style={{ width: `${(c.long / tot) * 100}%`, background: 'var(--long)' }} /><i style={{ width: `${(c.short / tot) * 100}%`, background: 'var(--short)' }} /></div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}><span className="pos">{c.long} long</span><span className="neg">{c.short} short</span></div>
                <div className="sub" style={{ marginTop: 6 }}>
                  {c.long >= c.short && c.avg_entry_long != null ? `Avg entry long ${price(c.avg_entry_long)}` : c.avg_entry_short != null ? `Avg entry short ${price(c.avg_entry_short)}` : ''}
                  {c.smi != null ? ` · SMI ${Math.round(c.smi)} ${lean}` : ' · SMI n/a'}{!c.on_perpl ? ' · not on Perpl' : ''}
                </div>
                <div className="acts">
                  <button className="btn" onClick={() => setSeeWallets(seeWallets === c.coin ? null : c.coin)}>See the {c.wallets.length} wallet{c.wallets.length === 1 ? '' : 's'}</button>
                  {c.on_perpl && c.wallets[0] && <button className="btn primary" onClick={() => onOpenWallet(c.wallets[0].venue, c.wallets[0].wallet)} title="Opens the wallet with the largest position; Mirror at my size is in its open positions">Mirror a position</button>}
                </div>
                {seeWallets === c.coin && (
                  <div className="wlist">
                    {c.wallets.map((w) => (
                      <div key={w.wallet}><button className="btn sm ghost" style={{ padding: 0, height: 'auto', color: 'var(--accent-text)' }} onClick={() => onOpenWallet(w.venue, w.wallet)}>{shortAddr(w.wallet)}</button>
                        <span><span className={w.side === 'long' ? 'pos' : 'neg'}>{w.side}</span> {usd(w.size_usd, false, true)}{w.leverage ? ` ${w.leverage}x` : ''}</span></div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {open && open.coins.length > 4 && <div className="sub" style={{ marginTop: 10 }}>{showAllCoins ? '' : `+ ${open.coins.length - 4} more coins · `}<button className="btn sm ghost" style={{ padding: 0, color: 'var(--accent-text)' }} onClick={() => setShowAllCoins((v) => !v)}>{showAllCoins ? 'show fewer' : 'show all'}</button></div>}
      </div>

      <MobileSheetB label="Filters" title="Filter trades" summary={[venue !== 'all' ? venue : null, coin || null, side || null, result !== 'all' ? result : null, withinWords[within]].filter(Boolean).join(' · ')}>
        <div className="ft-filters">
          <PillSelect label="Venue" value={venue} opts={['all', 'hl', 'perpl']} fmt={(v) => (v === 'all' ? 'All' : v === 'hl' ? 'Hyperliquid' : 'Perpl')} onChange={(v) => set('tvenue', v, 'all')} />
          <PillSelect label="Coin" value={coin} opts={['', ...coins]} fmt={(v) => v || 'Any'} onChange={(v) => set('tcoin', v, '')} />
          <PillSelect label="Side" value={side} opts={['', 'long', 'short']} fmt={(v) => (v ? v[0].toUpperCase() + v.slice(1) : 'Both')} onChange={(v) => set('tside', v, '')} />
          <PillSelect label="Result" value={result} opts={[...RESULT]} fmt={(v) => (v === 'all' ? 'Wins and losses' : v === 'wins' ? 'Wins only' : 'Losses only')} onChange={(v) => set('result', v, 'all')} />
          <PillSelect label="Min PnL" value={minPnl} opts={MINPNL} fmt={(v) => (v ? usd(v, false, true) : 'Any')} onChange={(v) => set('minPnl', v, 0)} />
          <PillSelect label="Min PnL %" value={minPct} opts={MINPCT} fmt={(v) => (v ? `${v}%` : 'Any')} onChange={(v) => set('minPnlPct', v, 0)} />
          <PillSelect label="Hold" value={hold} opts={[...HOLD]} fmt={(v) => ({ '': 'Any', scalper: 'Under 1 h', intraday: '1 to 24 h', swing: 'Over 1 day' }[v] ?? v)} onChange={(v) => set('thold', v, '')} />
          <PillSelect label="Closed within" value={within} opts={[...WITHIN]} fmt={(v) => withinWords[v]} onChange={(v) => set('within', v, '24h')} />
          <PillSelect label="Label" value={label} opts={['', 'consistent', 'asymmetric']} fmt={(v) => (v === 'consistent' ? 'Steady' : v === 'asymmetric' ? 'Big winners' : 'Both')} onChange={(v) => set('tlabel', v, '')} />
          {isAuthenticated && <button className="pill" aria-pressed={watched} onClick={() => set('watched', !watched, false)}><b>Only wallets I watch</b></button>}
          <button className="btn" onClick={reset}>Reset</button>
          {!phone && <span className="sub ft-count">{meta.count} trades closed in {withinWords[within]} · {meta.wins} wins · newest first</span>}
        </div>
      </MobileSheetB>
      {phone && <div className="sub">{meta.count} trades closed in {withinWords[within]} · {meta.wins} wins · newest first</div>}

      {err && <div className="ft-empty" style={{ color: 'var(--short)' }}>{err}</div>}
      {loading && rows.length === 0 ? <div className="ft-empty">Loading trades…</div> : rows.length === 0 ? (
        <div className="ft-empty">No closed trade from a featured wallet in this window{within !== '30d' ? ' yet. Try a longer window.' : '.'}</div>
      ) : phone ? (
        <div className="mlist">
          {rows.map((t) => {
            const [es, es2] = entryStyle(t);
            return (
              <div className={`mcard${fresh.has(t.id) ? ' new-row' : ''}`} key={t.id} onClick={() => setDetail(t.id)}>
                <div className="top"><VenueBadge venue={t.venue} /><span className="mono" style={{ fontWeight: 600, fontSize: 13 }}>{shortAddr(t.wallet)}</span><LabelChips label={t.label} /><span className="sub" style={{ marginLeft: 'auto' }}>{agoWords(t.close_time)}</span></div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, alignItems: 'baseline' }}>
                  <span><b>{t.coin}</b> <span className={t.side === 'long' ? 'pos' : 'neg'}>{t.side === 'long' ? 'Long' : 'Short'}</span> <span className="sub">{t.on_perpl ? 'on Perpl' : 'not on Perpl'}</span></span>
                  <span className={t.pnl_usd >= 0 ? 'pos' : 'neg'}>{usd(t.pnl_usd, true)} <span className="sub">{t.pnl_pct_margin != null ? `${pct(t.pnl_pct_margin, 1)} of margin` : `${pct(t.ret_pct, 2)} move`}</span></span>
                </div>
                <div className="sub" style={{ marginTop: 4 }}><span className="mono">{price(t.avg_entry)} → {price(t.avg_exit)}</span> · {holdWords(t.hold_sec)} · {usd(t.max_size_usd, false, true)}{t.leverage ? ` · ${t.leverage}x` : ''} · {es.toLowerCase()}, {es2}</div>
                <div className="acts" style={{ display: 'flex', gap: 8 }} onClick={(e) => e.stopPropagation()}>
                  <button className="btn primary" onClick={() => copy(t)}>Copy wallet</button>
                  <button className="btn" onClick={() => onWatch(t)}>{watchlist.isWatched(t.wallet, t.venue) ? 'Watching' : 'Watch'}</button>
                  <button className="btn" onClick={() => setDetail(t.id)}>Chart</button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="ft-table-wrap">
          <table className="ft-table">
            <thead><tr><th>Closed</th><th>Trader</th><th>Trade</th><th>Result</th><th>Entry → exit</th><th>Hold</th><th>Size · lev</th><th>Entry style</th><th>Context at entry</th><th></th></tr></thead>
            <tbody>
              {rows.map((t) => {
                const [es, es2] = entryStyle(t);
                const [cx, cx2] = contextWords(t);
                return (
                  <tr key={t.id} className={fresh.has(t.id) ? 'new-row' : ''} onClick={() => setDetail(t.id)}>
                    <td title={t.close_time.replace('T', ' ').slice(0, 19) + ' UTC'}>{agoWords(t.close_time)}</td>
                    <td><div className="trader"><VenueBadge venue={t.venue} /><div><div className="nm">{shortAddr(t.wallet)}{t.display_name ? <span className="sub"> {t.display_name}</span> : null}</div><LabelChips label={t.label} oneBigWin={t.one_big_win} /></div></div></td>
                    <td><span style={{ fontWeight: 700 }}>{t.coin}</span> <span className={t.side === 'long' ? 'pos' : 'neg'}>{t.side === 'long' ? 'Long' : 'Short'}</span> <span className="sub">{t.on_perpl ? 'on Perpl ✓' : 'not on Perpl'}</span></td>
                    <td><div className={t.pnl_usd >= 0 ? 'pos' : 'neg'}>{usd(t.pnl_usd, true)}</div><div className="sub">{t.pnl_pct_margin != null ? `${pct(t.pnl_pct_margin, 1)} of margin` : `${pct(t.ret_pct, 2)} price move`}{t.liquidation ? ' · liquidated' : ''}</div></td>
                    <td className="mono">{price(t.avg_entry)} → {price(t.avg_exit)}<div className="sub" style={{ fontFamily: 'var(--font)' }}>{t.n_adds ? `${t.n_adds} add${t.n_adds > 1 ? 's' : ''}` : '1 open'}, {t.n_closes} close{t.n_closes === 1 ? '' : 's'}</div></td>
                    <td>{holdWords(t.hold_sec)}</td>
                    <td>{usd(t.max_size_usd, false, true)}{t.leverage ? ` · ${t.leverage}x` : <span className="sub"> · lev n/a</span>}</td>
                    <td>{es}<div className="sub">{es2}</div></td>
                    <td>{cx}<div className="sub">{cx2}</div></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="acts">
                        <button className="btn" onClick={() => setDetail(t.id)}>Chart</button>
                        <button className="btn" aria-pressed={watchlist.isWatched(t.wallet, t.venue)} disabled={!isAuthenticated} onClick={() => onWatch(t)}>{watchlist.isWatched(t.wallet, t.venue) ? 'Watching' : 'Watch'}</button>
                        <button className="btn primary" onClick={() => copy(t)}>Copy wallet</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {meta.next && <div><button className="btn" onClick={() => load(meta.next)}>Load older trades</button></div>}

      {detail != null && <TradeDetailDrawer id={detail} onClose={() => setDetail(null)} onCopy={(t) => { setDetail(null); copy(t); }} onWallet={(t) => { setDetail(null); onOpenWallet(t.venue, t.wallet); }} />}
    </>
  );
}
