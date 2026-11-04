import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist } from '@/hooks/useCopyV1';
import { useToast } from '@/components/common/Toast';
import AutoCopyButton from '@/components/copy/AutoCopyButton';
import { getAutoSettings } from '@/lib/copyAutoApi';
import { getUtCluster, getUtWallet, getUtWallets, type UtCluster, type UtEntry, type UtLevel, type UtRow, type UtSummary, type UtWallet } from '@/lib/utApi';
import { agoWords, shortAddr } from '@/lib/featuredView';
import './ut.css';

// Unusual timing (design/web/index.html + app.js, ported): cards, filters,
// sortable list, dossier and cluster drawers on hash routes #w-<wallet> and
// #c-<id>, the market tell-tales table and the "How to read this page" block.
// Every number comes from /api/ut/*; nothing is computed here beyond display.

const LEVEL_LABEL: Record<UtLevel, string> = { watch: 'Watch', strong: 'Strong', cluster: 'Cluster' };
const EMPTY_FIRST = 'First scores land after the daily pass';

type Filters = { level: '' | UtLevel; hit: number; ev: number; age: '' | '30' | '90'; coin: string; open: boolean; act: '' | '1' | '24' | '168' };
type SortKey = 'wallet' | 'score' | 'hit_rate' | 'avg_move_2h' | 'events_preceded' | 'misses' | 'age_days' | 'coins' | 'open' | 'last_entry_at';
const F0: Filters = { level: '', hit: 0, ev: 0, age: '', coin: '', open: false, act: '' };

const money = (n: number | null | undefined, signed = true): string => {
  if (n == null) return 'n/a';
  const a = Math.abs(n);
  const s = a >= 1e6 ? `$${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M` : a >= 1e3 ? `$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K` : `$${a.toFixed(0)}`;
  return (signed ? (n > 0 ? '+' : n < 0 ? '-' : '') : (n < 0 ? '-' : '')) + s;
};
const pctTxt = (n: number | null | undefined, digits = 1): string => (n == null ? 'n/a' : `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`);
const rate = (n: number | null | undefined): string => (n == null ? 'n/a' : `${Math.round(n * 100)}%`);
const ago = (iso: string | null | undefined): string => (iso ? agoWords(iso) : 'none yet');
const utc = (iso: string | null | undefined): string => {
  if (!iso) return '-';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleString('en-GB', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).replace(',', '');
};
const dayTxt = (n: number | null | undefined, known = true): string => (n == null || !known ? 'unknown' : `${Math.round(n)} d`);
const lvClass = (l: UtLevel) => `chip lv-${l}`;
const moveCls = (n: number | null | undefined, side: 'long' | 'short') => (n == null ? 'sub' : ((side === 'short' ? n < 0 : n > 0) ? 'pos num' : 'neg num'));
const moveTxt = (n: number | null | undefined) => (n == null ? 'open' : pctTxt(n));

function hashOf(): { kind: 'w' | 'c'; id: string } | null {
  const h = (typeof location !== 'undefined' ? location.hash : '').replace('#', '');
  if (/^w-0x[0-9a-fA-F]{40}$/.test(h)) return { kind: 'w', id: h.slice(2).toLowerCase() };
  if (/^c-\d+$/.test(h)) return { kind: 'c', id: h.slice(2) };
  return null;
}
const go = (hash: string) => { location.hash = hash; };
const closeHash = () => { if (location.hash) history.replaceState(null, '', location.pathname + location.search); window.dispatchEvent(new HashChangeEvent('hashchange')); };

export default function UnusualTimingTab({ summary }: { summary: UtSummary | null }) {
  const { isAuthenticated } = useAuth();
  const watchlist = useWatchlist(isAuthenticated);
  const toast = useToast();
  const [f, setF] = useState<Filters>(F0);
  const [sort, setSort] = useState<SortKey>('score');
  const [dir, setDir] = useState<'asc' | 'desc'>('desc');
  const params = useMemo(() => {
    const p: Record<string, string | number | boolean> = { sort, dir };
    if (f.level) p.level = f.level;
    if (f.hit) p.min_hit = f.hit;
    if (f.ev) p.min_events = f.ev;
    if (f.age) p.max_age_days = Number(f.age);
    if (f.coin) p.coin = f.coin;
    if (f.open) p.has_open = true;
    if (f.act) p.active_within_h = Number(f.act);
    return p;
  }, [f, sort, dir]);
  const q = useQuery({ queryKey: ['ut-wallets', params], queryFn: () => getUtWallets(params), staleTime: 60_000, retry: false });
  const allCoins = useQuery({ queryKey: ['ut-wallets', { sort: 'score', dir: 'desc' }], queryFn: () => getUtWallets({ sort: 'score', dir: 'desc' }), staleTime: 60_000, retry: false });
  const coinOptions = useMemo(() => Array.from(new Set((allCoins.data?.rows ?? []).flatMap((r) => r.coins))).sort(), [allCoins.data]);
  const rows = q.data?.rows ?? [];
  const nothingYet = !!q.data && q.data.total === 0;

  // drawer routing
  const [route, setRoute] = useState(hashOf());
  useEffect(() => {
    const on = () => setRoute(hashOf());
    window.addEventListener('hashchange', on);
    on();
    return () => window.removeEventListener('hashchange', on);
  }, []);
  useEffect(() => {
    if (!route) return;
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') closeHash(); };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [route]);

  const onWatch = async (wallet: string, what: string) => {
    if (!isAuthenticated) { toast.error('Sign in to watch a wallet'); return; }
    try {
      if (watchlist.isWatched(wallet, 'hl')) { await watchlist.unwatch(wallet, 'hl'); toast.info(`Stopped watching ${what}`); }
      else { await watchlist.watch(wallet, 'hl'); toast.success(`Watching ${what}. Telegram alerts for this list come in a later phase.`); }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Could not update the watch list');
    }
  };
  const isWatched = (w: string) => watchlist.isWatched(w, 'hl');

  const clickSort = (k: SortKey) => {
    if (sort === k) setDir(dir === 'desc' ? 'asc' : 'desc');
    else { setSort(k); setDir(k === 'wallet' || k === 'age_days' || k === 'misses' ? 'asc' : 'desc'); }
  };
  const Th = ({ k, children, title }: { k: SortKey; children: ReactNode; title?: string }) => (
    <th className={sort === k ? `sorted ${dir}` : undefined} onClick={() => clickSort(k)} title={title}>{children}</th>
  );
  const byLevel = summary?.by_level;

  return (
    <div className="ft-ut">
      <div className="cards">
        <div className="card"><div className="lab">Flagged wallets</div><div className="big num">{summary ? summary.flagged : '…'}</div>
          <div className="sub">{byLevel ? `${byLevel.watch} Watch · ${byLevel.strong} Strong · ${summary!.in_clusters} in clusters` : EMPTY_FIRST}</div></div>
        <div className="card"><div className="lab">Clusters</div><div className="big num">{summary ? summary.clusters : '…'}</div>
          <div className="sub">{summary && summary.clusters ? `largest has ${summary.largest_cluster} wallets` : 'none yet'}</div></div>
        <div className="card"><div className="lab">Open right now</div><div className="big num">{summary ? summary.open_now.count : '…'}</div>
          <div className="sub">{summary ? `${money(summary.open_now.notional, false)} notional · ${summary.open_now.short} short, ${summary.open_now.long} long` : ''}</div></div>
        <div className="card"><div className="lab">Last alert</div><div className="big num">{summary?.last_alert ? ago(summary.last_alert.at) : 'none yet'}</div>
          <div className="sub">{summary?.last_alert ? `${shortAddr(summary.last_alert.wallet)} ${summary.last_alert.side} ${summary.last_alert.coin} ${money(summary.last_alert.notional, false)}${summary.last_alert.leverage ? `, ${Math.round(summary.last_alert.leverage)}x` : ''}` : 'an admin message goes out on every entry over $100K by a flagged wallet'}</div></div>
      </div>

      <div className="filters" id="ut-filters">
        <label className="pill">Level <select value={f.level} onChange={(e) => setF({ ...f, level: e.target.value as Filters['level'] })}>
          <option value="">All</option><option value="watch">Watch</option><option value="strong">Strong</option><option value="cluster">Cluster</option></select></label>
        <label className="pill">Min hit rate <select value={f.hit} onChange={(e) => setF({ ...f, hit: Number(e.target.value) })}>
          <option value={0}>Any</option><option value={50}>50%</option><option value={60}>60%</option><option value={75}>75%</option></select></label>
        <label className="pill">Min events <select value={f.ev} onChange={(e) => setF({ ...f, ev: Number(e.target.value) })}>
          <option value={0}>Any</option><option value={2}>2</option><option value={3}>3</option><option value={5}>5</option></select></label>
        <label className="pill">Wallet age <select value={f.age} onChange={(e) => setF({ ...f, age: e.target.value as Filters['age'] })}>
          <option value="">Any</option><option value="30">Under 30 d</option><option value="90">Under 90 d</option></select></label>
        <label className="pill">Coin <select value={f.coin} onChange={(e) => setF({ ...f, coin: e.target.value })}>
          <option value="">Any</option>{coinOptions.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
        <button className={`pill${f.open ? ' on' : ''}`} aria-pressed={f.open} onClick={() => setF({ ...f, open: !f.open })}>Has open position</button>
        <label className="pill">Active within <select value={f.act} onChange={(e) => setF({ ...f, act: e.target.value as Filters['act'] })}>
          <option value="">Any</option><option value="1">1 h</option><option value="24">24 h</option><option value="168">7 d</option></select></label>
        <button className="reset" onClick={() => setF(F0)}>Reset</button>
      </div>

      <div className="tablewrap">
        <table id="ut-table">
          <thead><tr>
            <Th k="wallet">Wallet</Th>
            <Th k="score">Level · score</Th>
            <Th k="hit_rate" title="Entries followed by a big move within 2 h, against the coin's own base rate">Early before big moves</Th>
            <Th k="avg_move_2h">Avg move after entry</Th>
            <Th k="events_preceded">Events preceded</Th>
            <Th k="misses">Misses · worst loss</Th>
            <Th k="age_days">Age · funder</Th>
            <Th k="coins">Coins</Th>
            <Th k="open">Open now</Th>
            <Th k="last_entry_at">Last entry</Th>
            <th />
          </tr></thead>
          <tbody>
            {rows.map((w) => (
              <tr key={w.wallet} data-w={w.wallet} onClick={() => go(`w-${w.wallet}`)}>
                <td><span className={`venue${w.venue === 'perpl' ? ' p' : ''}`}>{w.venue === 'perpl' ? 'P' : 'H'}</span><span className="mono">{shortAddr(w.wallet)}</span></td>
                <td>
                  {w.cluster_id
                    ? <span className={`${lvClass(w.level)} link`} title={`Open cluster ${w.cluster_id}`} onClick={(e) => { e.stopPropagation(); go(`c-${w.cluster_id}`); }}>
                        {w.level === 'cluster' ? `Cluster C-${w.cluster_id}` : `${LEVEL_LABEL[w.level]} · C-${w.cluster_id}`}</span>
                    : <span className={lvClass(w.level)}>{LEVEL_LABEL[w.level]}</span>} <b className="num">{Math.round(w.score)}</b>
                </td>
                <td><b className="num">{w.hits} of {w.entries}</b> <span className="sub">{rate(w.hit_rate)} vs {w.base_rate != null ? (w.base_rate * 100).toFixed(1) : '?'}% by chance · {w.odds_text}</span></td>
                <td>{w.avg_move_2h == null ? <span className="sub">n/a</span> : <><span className={`${w.avg_move_2h >= 0 ? 'pos' : 'neg'} num`}>{pctTxt(w.avg_move_2h)}</span> <span className="sub">2 h · {pctTxt(w.avg_move_24h)} 24 h</span></>}</td>
                <td><b className="num">{w.events_preceded}</b> {w.event_kinds.slice(0, 3).map((e) => <span className="ev" key={e}>{e}</span>)}{w.event_kinds.length > 3 && <span className="sub">+{w.event_kinds.length - 3}</span>}</td>
                <td className="num">{w.misses} · <span className="neg">{w.worst_loss_usd == null ? 'n/a' : money(Math.min(0, w.worst_loss_usd))}</span></td>
                <td className="num">{dayTxt(w.age_days, w.age_known)} <span className="sub">{w.funder_label ?? (w.ledger_checked ? 'no deposit found' : 'funder pending')}</span></td>
                <td>{w.coins.slice(0, 4).join(', ')}{w.coins.length > 4 ? ` +${w.coins.length - 4}` : ''}</td>
                <td>{w.open ? <><span className={w.open.side === 'short' ? 'neg' : 'pos'}>{w.open.coin} {w.open.side}</span> {money(w.open.notional, false)} <span className="sub">{w.open.leverage ? `${Math.round(w.open.leverage)}x` : ''}</span></> : <span className="sub">none</span>}</td>
                <td className="num">{ago(w.last_entry_at)}</td>
                <td><button className="btn sm" onClick={(e) => { e.stopPropagation(); go(`w-${w.wallet}`); }}>Dossier</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.isLoading && <div className="empty">Loading…</div>}
        {q.isError && <div className="empty">Unusual timing data is unavailable right now. <button className="btn sm" onClick={() => q.refetch()}>Retry</button></div>}
        {!q.isLoading && !q.isError && rows.length === 0 && (
          <div className="empty">{nothingYet ? `${EMPTY_FIRST}.${q.data?.scored ? ` ${q.data.scored.toLocaleString()} wallets scored so far, none listed yet.` : ''}` : 'No wallet matches these filters.'}</div>
        )}
      </div>

      <div className="two">
        <div className="card">
          <div className="lab">Market tell-tales (coins where flagged wallets are piling in)</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ minWidth: 720, marginTop: 6 }}>
              <thead><tr><th>Coin</th><th>Drift score</th><th>Funding 48 h</th><th>OI 6 h</th><th>Return 12 h vs normal</th><th>Flagged entries 6 h</th><th>Next event</th></tr></thead>
              <tbody>
                {(summary?.drift ?? []).map((d) => (
                  <tr key={d.coin} style={{ cursor: 'default' }}>
                    <td>{d.coin}</td>
                    <td><span className="bar"><i style={{ width: `${Math.min(100, d.score)}%` }} /></span>{Math.round(d.score)}</td>
                    <td className={d.funding_trend_48h == null ? 'sub' : d.funding_trend_48h < 0 ? 'neg' : d.funding_trend_48h > 0 ? 'pos' : ''}>
                      {d.funding_trend_48h == null ? 'n/a' : Math.abs(d.funding_trend_48h) < 0.00005 ? 'flat' : d.funding_trend_48h < 0 ? (d.funding_trend_48h < -0.0003 ? 'falling fast' : 'falling') : (d.funding_trend_48h > 0.0003 ? 'rising fast' : 'rising')}</td>
                    <td className="num">{pctTxt(d.oi_change_6h, 0)}</td>
                    <td className="num">{d.abn_ret_12h == null ? 'n/a' : `${d.abn_ret_12h > 0 ? '+' : ''}${d.abn_ret_12h.toFixed(1)}σ`}</td>
                    <td>{d.flagged_entries_6h}</td>
                    <td>{summary?.next_event ? `${summary.next_event.note?.split(' (')[0] ?? summary.next_event.kind}, ${ago(summary.next_event.time).replace(' ago', '')}` : 'none known'}</td>
                  </tr>
                ))}
                {!summary?.drift?.length && <tr><td colSpan={7} className="sub">Drift rows appear after the first hourly open-interest snapshot.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card howto">
          <div className="lab">How to read this page</div>
          <p><b>Early before big moves</b> is how often the wallet entered within an hour before a 5%+ move in its direction, next to what chance would give for the same coins. <span className="chip lv-watch">Watch</span> is above-chance timing with at least 4 hits. <span className="chip lv-strong">Strong</span> is timing you would see by chance less than 1 time in 1,000, plus at least two other signals. <span className="chip lv-cluster">Cluster</span> is a Strong wallet that moves with others.</p>
          <p>Click a row for the wallet's full file. This shows timing, not the reason for it. Misses and worst loss are always next to the hits.</p>
        </div>
      </div>

      {route?.kind === 'w' && <WalletDrawer address={route.id} scoresAsOf={summary?.scores_as_of ?? null} liveAt={summary?.live_at ?? null} onWatch={onWatch} isWatched={isWatched} />}
      {route?.kind === 'c' && <ClusterDrawer id={Number(route.id)} scoresAsOf={summary?.scores_as_of ?? null} onWatch={onWatch} isWatched={isWatched} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// drawer shell
// ---------------------------------------------------------------------------

function Drawer({ children }: { children: ReactNode }) {
  return (
    <>
      <div className="ut-scrim" onClick={closeHash} />
      <aside className="ut-drawer" role="dialog" aria-modal="true">{children}</aside>
    </>
  );
}

function CopyHalfButton({ wallet }: { wallet: string }) {
  const { isAuthenticated } = useAuth();
  const vis = useQuery({ queryKey: ['copy-auto-settings'], queryFn: getAutoSettings, enabled: !!isAuthenticated, staleTime: 60_000 });
  if (isAuthenticated && vis.data?.ui_visible) {
    return <AutoCopyButton leaderWallet={wallet} label="Copy (half size)" preset={{ halfSize: true, stopPct: 1.5 }} className="btn p" />;
  }
  return <button className="btn p" disabled title={isAuthenticated ? 'Auto-copy opens to everyone after the closed test' : 'Sign in to copy'}>Copy (half size)</button>;
}

// ---------------------------------------------------------------------------
// wallet dossier
// ---------------------------------------------------------------------------

function PriceChart({ d, entries }: { d: UtWallet; entries: UtEntry[] }) {
  const c = d.candles;
  if (!c || c.points.length < 2) return <div className="sub" style={{ marginTop: 8 }}>No price series stored yet for {d.coins[0] ?? 'this coin'}.</div>;
  const W = 900, H = 200, top = 12, bottom = 172;
  const t0 = c.points[0][0], t1 = c.points[c.points.length - 1][0];
  const ps = c.points.map((p) => p[1]);
  const lo = Math.min(...ps), hi = Math.max(...ps);
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const y = (v: number) => bottom - ((v - lo) / Math.max(1e-9, hi - lo)) * (bottom - top);
  const line = c.points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ');
  const coinEntries = entries.filter((e) => e.coin === c.coin);
  const marks = coinEntries.map((e) => {
    const t = Date.parse(e.time.endsWith('Z') ? e.time : e.time + 'Z') / 1000;
    const px = e.px ?? lo;
    return { x: x(t), y: y(Math.min(hi, Math.max(lo, px))), hit: !!e.hit, open: e.ret_2h == null, side: e.side, ev: e.event_kind };
  });
  const bandW = Math.max(3, (7200 / Math.max(1, t1 - t0)) * W);
  const d0 = new Date(t0 * 1000), d1 = new Date(t1 * 1000);
  const lab = (dt: Date) => dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price with the wallet entries marked">
      {marks.map((m, i) => <rect key={`b${i}`} className={m.hit ? 'win' : 'miss'} x={m.x} y={0} width={bandW} height={bottom} />)}
      <polyline className="price" points={line} />
      {marks.map((m, i) => (m.side === 'short'
        ? <polygon key={`m${i}`} className="short" opacity={m.open ? 0.5 : 1} points={`${m.x},${m.y - 10} ${m.x - 6},${m.y - 20} ${m.x + 6},${m.y - 20}`} />
        : <polygon key={`m${i}`} className="long" opacity={m.open ? 0.5 : 1} points={`${m.x},${m.y + 10} ${m.x - 6},${m.y + 20} ${m.x + 6},${m.y + 20}`} />))}
      {c.events.slice(0, 40).map((e) => <polygon key={`e${e.id}`} className="evm" points={`${x(e.t)},22 ${x(e.t) + 5},28 ${x(e.t)},34 ${x(e.t) - 5},28`}><title>{`${e.kind} ${e.direction}`}</title></polygon>)}
      {marks.filter((m) => !m.hit && !m.open).slice(0, 60).map((m, i) => <text key={`t${i}`} x={m.x} y={165} textAnchor="middle">miss</text>)}
      <text x={0} y={192}>{lab(d0)}</text><text x={W - 40} y={192}>{lab(d1)}</text>
    </svg>
  );
}

function WalletDrawer({ address, scoresAsOf, liveAt, onWatch, isWatched }: {
  address: string; scoresAsOf: string | null; liveAt: string | null; onWatch: (w: string, what: string) => void; isWatched: (w: string) => boolean;
}) {
  const q = useQuery({ queryKey: ['ut-wallet', address], queryFn: () => getUtWallet(address), staleTime: 60_000, retry: false });
  const d = q.data;
  if (q.isLoading) return <Drawer><div className="head"><div className="txt"><div className="summary">Loading the file for {shortAddr(address)}…</div></div><div className="acts"><button className="btn" onClick={closeHash} aria-label="Close">✕</button></div></div></Drawer>;
  if (!d) return <Drawer><div className="head"><div className="txt"><div className="summary">{(q.error as any)?.response?.status === 404 ? 'No unusual-timing record for this wallet.' : 'Could not load this wallet right now.'}</div></div><div className="acts"><button className="btn" onClick={closeHash} aria-label="Close">✕</button></div></div></Drawer>;
  const entries: UtEntry[] = (d as any).entries_list ?? ((d as any).entries as UtEntry[] | number) as UtEntry[];
  const list: UtEntry[] = Array.isArray(entries) ? entries : [];
  const hits = list.filter((e) => e.hit).length;
  const nEntries = d.hits + d.misses;
  const chance = d.base_rate != null ? Math.max(1, Math.round(nEntries * d.base_rate)) : null;
  const mainSide: 'long' | 'short' = d.open?.side ?? (list[0]?.side ?? 'short');
  const funderTxt = d.funder_label ? d.funder_label : d.funder ? 'an unlabelled address' : d.ledger_checked ? 'no deposit on record' : 'a source not read yet';
  const clusterTxt = d.cluster_id && d.cluster_size ? ` and moves together with ${d.cluster_size - 1} other wallet${d.cluster_size > 2 ? 's' : ''}` : '';
  const summary = `Entered ${d.hits} of its ${nEntries} measured trades within an hour before a big move, on ${d.coins.slice(0, 3).join(' and ') || 'its coins'}, when chance would give about ${chance ?? '?'}. `
    + `Wallet is ${d.age_days != null ? `${Math.round(d.age_days)} days old` : 'of unknown age'}, funded from ${funderTxt}${clusterTxt}. ${d.misses} misses, worst loss ${d.worst_loss_usd == null ? 'n/a' : money(Math.min(0, d.worst_loss_usd))}.`;
  return (
    <Drawer>
      <div className="head">
        <div className="txt">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className={`venue${d.venue === 'perpl' ? ' p' : ''}`}>{d.venue === 'perpl' ? 'P' : 'H'}</span>
            <span className="mono" style={{ fontSize: 15, fontWeight: 600 }}>{d.wallet}</span>
            <span className={lvClass(d.level)}>{d.level === 'cluster' && d.cluster_id ? `Cluster C-${d.cluster_id}` : LEVEL_LABEL[d.level]}</span>
            <span className="chip chip-grey">Score {Math.round(d.score)}</span>
          </div>
          <div className="summary">{summary}</div>
          <div className="sub" style={{ marginTop: 6 }}>This shows timing, not the reason for it. {d.level_words} Scores as of {scoresAsOf ?? d.scores_as_of ?? '…'} · live fields {liveAt ? agoWords(liveAt) : 'waiting'}</div>
        </div>
        <div className="acts">
          <CopyHalfButton wallet={d.wallet} />
          <button className="btn" onClick={() => onWatch(d.wallet, shortAddr(d.wallet))}>{isWatched(d.wallet) ? 'Watching' : 'Watch'}</button>
          {d.cluster_id && <button className="btn" onClick={() => go(`c-${d.cluster_id}`)}>Cluster C-{d.cluster_id}</button>}
          <button className="btn" onClick={closeHash} aria-label="Close">✕</button>
        </div>
      </div>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
        <div className="card"><div className="lab">Early before moves</div><div className="big num">{d.hits} of {nEntries}</div><div className="sub">{rate(d.hit_rate)} · base rate {d.base_rate != null ? (d.base_rate * 100).toFixed(1) : '?'}%</div></div>
        <div className="card"><div className="lab">Odds by chance</div><div className="big num">{d.odds_text}</div><div className="sub">binomial, per-coin base rates</div></div>
        <div className="card"><div className="lab">Avg move after entry</div><div className={`big num ${(d.avg_move_2h ?? 0) >= 0 ? 'pos' : 'neg'}`}>{pctTxt(d.avg_move_2h)}</div><div className="sub">in 2 h · {pctTxt(d.avg_move_24h)} in 24 h</div></div>
        <div className="card"><div className="lab">Events preceded</div><div className="big num">{d.events_preceded}</div><div className="sub">{d.event_kinds_n} kind{d.event_kinds_n !== 1 ? 's' : ''}{d.event_kinds.length ? `: ${d.event_kinds.join(', ')}` : ''}</div></div>
        <div className="card"><div className="lab">Wallet age</div><div className="big num">{dayTxt(d.age_days, d.age_known)}</div><div className="sub">first deposit {d.first_deposit_usd != null ? money(d.first_deposit_usd, false) : 'n/a'} · {d.funder_label ?? (d.ledger_checked ? 'unlabelled' : 'pending')}</div></div>
        <div className="card"><div className="lab">Moved the market</div><div className="big num">{d.moved_market_count} of {nEntries}</div><div className="sub">own size over 20% of 2 h venue volume, not counted as timing</div></div>
      </div>
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <div className="lab">Entries on the price chart · {d.candles?.coin ?? d.coins[0] ?? '-'}, last 90 days</div>
          <div className="sub">{mainSide === 'short' ? '▼ short entry' : '▲ long entry'} · ◆ event · shaded = 2 h after entry (red: hit, grey: miss)</div>
        </div>
        <PriceChart d={d} entries={list} />
      </div>
      <div className="two">
        <div className="card">
          <div className="lab">Every entry, newest first ({list.length}{list.length !== hits ? `, ${hits} hits` : ''})</div>
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ minWidth: 760 }}>
              <thead><tr><th>Time (UTC)</th><th>Coin</th><th>Side</th><th>Size · lev</th><th>Move after 2 h</th><th>Move after 24 h</th><th>Minutes before</th><th>Event</th><th>Closed</th><th>Result</th></tr></thead>
              <tbody>
                {list.map((e) => (
                  <tr key={e.id} style={{ cursor: 'default' }}>
                    <td className="num">{utc(e.time)}</td><td>{e.coin}</td>
                    <td className={e.side === 'short' ? 'neg' : 'pos'}>{e.side === 'short' ? 'Short' : 'Long'}{e.is_add ? <span className="sub"> add</span> : null}</td>
                    <td className="num">{money(e.notional, false)}{e.leverage ? ` · ${Math.round(e.leverage)}x` : ''}</td>
                    <td><span className={moveCls(e.ret_2h, e.side)}>{moveTxt(e.ret_2h)}</span></td>
                    <td><span className={moveCls(e.ret_24h, e.side)}>{moveTxt(e.ret_24h)}</span></td>
                    <td className="num">{e.minutes_before_event == null ? <span className="sub">-</span> : <b>{e.minutes_before_event}</b>}</td>
                    <td>{e.event_kind ? <span className="ev">{e.event_kind}</span> : <span className="sub">-</span>}</td>
                    <td className="num">{e.closed_at ? (e.close_after_peak_min != null ? `${e.close_after_peak_min >= 0 ? Math.round(e.close_after_peak_min) + ' m after peak' : Math.round(-e.close_after_peak_min) + ' m before peak'}` : utc(e.closed_at)) : <span className="sub">open</span>}</td>
                    <td className={`num ${(e.pnl ?? 0) >= 0 ? 'pos' : 'neg'}`}>{e.pnl == null ? <span className="sub">{e.closed_at ? 'n/a' : 'open'}</span> : money(e.pnl)}{e.moved_market ? <span className="sub"> moved</span> : null}</td>
                  </tr>
                ))}
                {!list.length && <tr><td colSpan={10} className="sub">No entries in the last 90 days.</td></tr>}
              </tbody>
            </table>
          </div>
          {d.moved_market_count > 0 && <div className="sub" style={{ marginTop: 8 }}>{d.moved_market_count} entr{d.moved_market_count > 1 ? 'ies are' : 'y is'} the wallet's own market impact and not counted as timing.</div>}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          <div className="card"><div className="lab">Signals (measured values)</div>
            {d.signals_detail.map((s) => (
              <div className="kv" key={s.key}><span><span className="chip" style={s.pass ? { background: 'var(--long)', color: '#fff' } : { background: 'var(--s2)', color: 'var(--muted)' }}>{s.pass ? '✓' : '·'}</span> {s.name}</span><span className="num">{s.value}</span></div>
            ))}
          </div>
          <div className="card"><div className="lab">Misses and risk</div>
            <div className="kv"><span>Misses</span><span className="num">{d.misses} of {nEntries}</span></div>
            <div className="kv"><span>Worst loss</span><span className="neg num">{d.worst_loss_usd == null ? 'n/a' : money(Math.min(0, d.worst_loss_usd))}</span></div>
            <div className="kv"><span>Net profit 90 d</span><span className={`num ${(d.net_profit_90d ?? 0) >= 0 ? 'pos' : 'neg'}`}>{money(d.net_profit_90d)}</span></div>
            <div className="kv"><span>Median leverage</span><span className="num">{d.median_leverage != null ? `${Math.round(d.median_leverage)}x` : 'n/a'}</span></div>
            <div className="kv"><span>Liquidations</span><span className="num">{d.liquidations}</span></div>
          </div>
          <div className="card warn"><div className="lab">Before you copy</div>
            <div style={{ fontSize: 13, lineHeight: 1.55, marginTop: 6 }}>Right rarely and hugely. The entry may itself move a thin book, and a {d.median_leverage != null ? `${Math.round(d.median_leverage)}x` : 'high-leverage'} position copied at your size behaves differently. Copy starts at half your usual margin with a 1.5% stop.</div>
          </div>
        </div>
      </div>
    </Drawer>
  );
}

// ---------------------------------------------------------------------------
// cluster view
// ---------------------------------------------------------------------------

function ClusterDrawer({ id, scoresAsOf, onWatch, isWatched }: {
  id: number; scoresAsOf: string | null; onWatch: (w: string, what: string) => void; isWatched: (w: string) => boolean;
}) {
  const q = useQuery({ queryKey: ['ut-cluster', id], queryFn: () => getUtCluster(id), staleTime: 60_000, retry: false });
  const c = q.data;
  const close = useCallback(() => closeHash(), []);
  if (q.isLoading) return <Drawer><div className="head"><div className="txt"><div className="summary">Loading cluster C-{id}…</div></div><div className="acts"><button className="btn" onClick={close} aria-label="Close">✕</button></div></div></Drawer>;
  if (!c) return <Drawer><div className="head"><div className="txt"><div className="summary">Cluster not found.</div></div><div className="acts"><button className="btn" onClick={close} aria-label="Close">✕</button></div></div></Drawer>;
  const ms = c.members;
  const lead = ms.find((m) => m.wallet === c.lead_wallet) ?? ms[0];
  const n = ms.length;
  const R = 95, cx = 190, cy = 125;
  const pos = ms.map((_m, i) => [cx + R * Math.cos((2 * Math.PI * i) / n - Math.PI / 2), cy + R * Math.sin((2 * Math.PI * i) / n - Math.PI / 2)] as [number, number]);
  const idx = (w: string) => ms.findIndex((m) => m.wallet === w);
  const maxNot = Math.max(1, ...ms.map((m) => (m.open?.notional ?? 0)));
  const onEvents = c.co_entries.filter((x) => x.event).length;
  const sharedFunder = c.edges.filter((e) => e.shared_funder).length;
  const summaryTxt = `${n} wallets that entered the same coin on the same side within 60 seconds of each other ${c.co_entries.length} time${c.co_entries.length === 1 ? '' : 's'}, ${onEvents} of those within an hour before a big move.`
    + (sharedFunder ? ` ${sharedFunder} pair${sharedFunder > 1 ? 's' : ''} share a funder.` : '')
    + (c.events.length ? ` Together they were early on ${c.events.length} event${c.events.length > 1 ? 's' : ''}.` : '') + ' Treat them as one trader.';
  return (
    <Drawer>
      <div className="head">
        <div className="txt">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="chip lv-cluster">Cluster C-{c.id}</span><span className="chip chip-grey">{n} wallets</span>
            <span className="chip chip-grey">Combined {money(ms.reduce((s, m) => s + (m.open?.notional ?? 0), 0), false)} open</span>
          </div>
          <div className="summary">{summaryTxt}</div>
          <div className="sub" style={{ marginTop: 6 }}>Edges: co-entries (solid, width = count), same funder (dashed). Scores as of {scoresAsOf ?? c.scores_as_of ?? '…'}</div>
        </div>
        <div className="acts">
          <button className="btn" onClick={() => lead && onWatch(lead.wallet, `the lead wallet ${shortAddr(lead.wallet)}`)}>{lead && isWatched(lead.wallet) ? 'Watching cluster' : 'Watch cluster'}</button>
          <button className="btn" onClick={close} aria-label="Close">✕</button>
        </div>
      </div>
      <div className="two" style={{ gridTemplateColumns: '1fr 1.4fr' }}>
        <div className="card"><div className="lab">How they connect</div>
          <svg className="graph" viewBox="0 0 380 260" style={{ width: '100%', height: 'auto', marginTop: 8 }} role="img" aria-label="Wallets linked by co-entry and funder edges">
            {c.edges.map((e) => {
              const a = idx(e.a), b = idx(e.b);
              if (a < 0 || b < 0) return null;
              return (
                <g key={`${e.a}${e.b}`}>
                  {e.co_entries > 0 && <line className="edge" x1={pos[a][0]} y1={pos[a][1]} x2={pos[b][0]} y2={pos[b][1]} strokeWidth={Math.min(6, 1 + e.co_entries)} />}
                  {e.shared_funder && <line className="edge fund" x1={pos[a][0] + 4} y1={pos[a][1] + 4} x2={pos[b][0] + 4} y2={pos[b][1] + 4} strokeWidth={2} />}
                </g>
              );
            })}
            {ms.map((m, i) => {
              const r = 14 + 14 * Math.sqrt((m.open?.notional ?? 0) / maxNot);
              const col = m.level === 'cluster' ? 'var(--ut-cluster-bg)' : m.level === 'strong' ? 'var(--ut-strong-fg)' : 'var(--ut-watch-fg)';
              return (
                <g key={m.wallet} style={{ cursor: 'pointer' }} onClick={() => go(`w-${m.wallet}`)}>
                  <circle cx={pos[i][0]} cy={pos[i][1]} r={r} fill={col} />
                  <text className="nlab" x={pos[i][0]} y={pos[i][1] + 4} textAnchor="middle" style={{ fill: m.level === 'cluster' ? 'var(--ut-cluster-fg)' : '#fff' }}>{m.wallet.slice(0, 6)}</text>
                  <text x={pos[i][0]} y={pos[i][1] + r + 12} textAnchor="middle">{m.wallet === c.lead_wallet ? 'lead · ' : ''}score {Math.round(m.score)}</text>
                </g>
              );
            })}
          </svg>
          <div className="sub">Circle size = open notional now. Labelled exchange hot wallets never count as a shared funder.</div>
        </div>
        <div className="card"><div className="lab">Members</div>
          <div style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ minWidth: 620 }}>
              <thead><tr><th>Wallet</th><th>Level · score</th><th>Early</th><th>Age · funder</th><th>Co-entries</th><th>Open now</th><th /></tr></thead>
              <tbody>
                {ms.map((m) => (
                  <tr key={m.wallet} data-member={m.wallet} onClick={() => go(`w-${m.wallet}`)}>
                    <td><span className={`venue${m.venue === 'perpl' ? ' p' : ''}`}>{m.venue === 'perpl' ? 'P' : 'H'}</span><span className="mono">{shortAddr(m.wallet)}</span></td>
                    <td><span className={lvClass(m.level)}>{LEVEL_LABEL[m.level]}</span> <b className="num">{Math.round(m.score)}</b></td>
                    <td className="num">{m.hits}/{m.hits + m.misses} <span className="sub">({m.base_rate != null ? (m.base_rate * 100).toFixed(1) : '?'}%)</span></td>
                    <td className="num">{dayTxt(m.age_days, m.age_known)} · <span className="sub">{m.funder_label ?? 'unknown'}</span></td>
                    <td className="num">{m.co_entries}</td>
                    <td>{m.open ? <span className={m.open.side === 'short' ? 'neg' : 'pos'}>{m.open.coin} {m.open.side} {money(m.open.notional, false)}</span> : <span className="sub">none</span>}</td>
                    <td><button className="btn sm" onClick={(e) => { e.stopPropagation(); go(`w-${m.wallet}`); }}>Dossier</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mini">
            <div><div className="lab">Combined 90 d profit</div><div className={`big num ${(c.combined_profit_90d ?? 0) >= 0 ? 'pos' : 'neg'}`}>{money(c.combined_profit_90d)}</div></div>
            <div><div className="lab">Combined misses</div><div className="big num">{c.combined_misses} of {c.combined_entries}</div><div className="sub">worst {c.combined_worst_loss_usd == null ? 'n/a' : money(Math.min(0, c.combined_worst_loss_usd))}</div></div>
            <div><div className="lab">Open now, same way</div><div className="big num">{c.same_way ? `${c.same_way.count} of ${n}` : `0 of ${n}`}</div><div className="sub">{c.same_way ? `${c.same_way.coin} ${c.same_way.side}, ${money(c.same_way.notional, false)}` : 'no open position'}</div></div>
          </div>
        </div>
      </div>
      <div className="card"><div className="lab">Co-entries, newest first ({c.co_entries.length})</div>
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table style={{ minWidth: 760 }}>
            <thead><tr><th>Time (UTC)</th><th>Coin · side</th><th>Wallets in</th><th>Spread</th><th>Combined size</th><th>Minutes before</th><th>Event</th><th>Move after 2 h</th><th>Result</th></tr></thead>
            <tbody>
              {c.co_entries.map((r, i) => (
                <tr key={i} style={{ cursor: 'default' }}>
                  <td className="num">{utc(r.time)}</td><td>{r.coin} {r.side}</td><td>{r.wallets_in} of {r.members}</td><td className="num">{r.spread_s} s</td>
                  <td className="num">{money(r.notional, false)}</td>
                  <td className="num">{r.minutes_before == null ? <span className="sub">-</span> : <b>{r.minutes_before}</b>}</td>
                  <td>{r.event ? <span className="ev">event</span> : <span className="sub">-</span>}</td>
                  <td><span className={moveCls(r.ret_2h, r.side)}>{moveTxt(r.ret_2h)}</span></td>
                  <td className={r.hit == null ? 'sub' : r.hit ? 'pos' : 'neg'}>{r.hit == null ? 'open' : r.hit ? 'hit' : 'miss'}</td>
                </tr>
              ))}
              {!c.co_entries.length && <tr><td colSpan={9} className="sub">No co-entries in the last 90 days.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <div className="two">
        <div className="card"><div className="lab">Events the cluster was early on ({c.events.length})</div>
          {c.events.map((e) => <div className="kv" key={e.id}><span><span className="ev">{e.kind}</span> {utc(e.time)} · {e.coin}</span><span className="num">{e.magnitude != null ? `${e.direction === 'down' ? '-' : '+'}${Math.abs(e.magnitude).toFixed(1)}% in 2 h` : e.direction}</span></div>)}
          {!c.events.length && <div className="sub" style={{ marginTop: 6 }}>No event shared by two or more members yet.</div>}
        </div>
        <div className="card warn"><div className="lab">Before you copy a cluster</div>
          <div style={{ fontSize: 13, lineHeight: 1.55, marginTop: 6 }}>Copy one wallet, not all {n}, or you take the same bet {n} times. The lead wallet carries the cluster score; the others add no independent signal. Combined size on {lead?.coins[0] ?? 'one coin'} can move the book on its own, so entries after theirs get a worse price.</div>
          {lead && <div style={{ marginTop: 10 }}><button className="btn" onClick={() => go(`w-${lead.wallet}`)}>Open lead wallet dossier</button></div>}
        </div>
      </div>
    </Drawer>
  );
}
