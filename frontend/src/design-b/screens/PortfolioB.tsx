import { oneAtATime } from '@/lib/oneAtATime';
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSignMessage } from 'wagmi';

import { useAuth } from '@/hooks/useAuth';
import { usePerplAccount } from '@/hooks/usePerplAccount';
import { hasAccountData, triggerPrice, triggerSize, triggersFor } from '@/lib/accountStore';
import api, { getCopyPerformance, getOrderHistory } from '@/lib/api';
import LiveDot from '@/components/common/LiveDot';
import {
  attachHoldTimes, buildAccountEvents, buildClosedTrades, byMarket, equityCurve, fillTotals,
  formatHold, marketSymbol, pnlByPeriod, realizedSince as realizedSinceTrades, startOfUtcToday,
  tradeStats, transferTotals, type ClosedTrade, type Grain,
} from '@/lib/perplAccountStats';
import { useCopyStore } from '@/stores/copyStore';
import { shortenAddress, formatUSD, formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import {
  buildEvents, buildLedger, dailyPnl, realizedSince, sinceMs, startOfUtcDay, totalsFor,
  type LedgerTrade,
} from '@/lib/perplLedger';
import {
  getPerplAccountHistory, getPerplFills, getPerplPositionHistory, hasPerplKey, pageAll,
} from '@/lib/perplRest';
import {
  closePosition, connectPerpl, getPerplPayload, getSession, openTradingWs, requireMarketConfig,
  setAccountId, tryAutoReconnect, warmupTradingConnection,
} from '@/lib/perplTrading';
import { useToast } from '@/components/common/Toast';
import SlTpEditor from '@/components/common/SlTpEditor';

// Design B: Portfolio. REBUILT 2026-10-08.
//
// It used to read /api/account-health once per mount for everything, compute
// the "Trading record" from copied trades only, build the daily result and the
// calendar by differencing 4-hourly equity snapshots, and show literal
// placeholders for Open orders / Trade history / Funding. The result was the
// page the owner reported: an empty donut, a copy-only record, "No account
// history yet", an empty calendar, and "Positions (0)" next to a real position.
//
// Now:
//   account, positions, margin, armed SL/TP  -> the shared account store
//     (one uncached chain read + the venue's own order frames; the SAME numbers
//     the Terminal and the Copy dashboard show, by construction);
//   trades, fees, funding, deposits, equity, calendar, trading record ->
//     Perpl's OWN authenticated reads (/v1/trading/fills and
//     /v1/trading/account-history), signed in the browser with the one-click
//     key. Realized P/L is FIFO-matched from real fills (lib/perplLedger), not
//     estimated and not snapshot-differenced.
// Nothing is invented: where Perpl has no data, the block says so.

const RISK_WORD: Record<string, string> = { safe: 'Low', warning: 'Medium', danger: 'High', critical: 'Critical' };
const RISK_POS: Record<string, number> = { safe: 0.16, warning: 0.5, danger: 0.8, critical: 0.95 };
const RISK_CLS: Record<string, string> = { safe: 'long-c', warning: 'amber-c', danger: 'short-c', critical: 'short-c' };

const SOURCE_LABEL: Record<string, string> = {
  manual: 'Terminal', terminal: 'Terminal', copy_trade: 'Copy', auto_copy: 'Auto-copy', other: 'Other',
};

function polar(cx: number, cy: number, r: number, deg: number) {
  const a = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function AccountRing({ available, margin, unrealized }: { available: number; margin: number; unrealized: number }) {
  const parts = [
    { v: Math.max(0, available), color: 'var(--accent)' },
    { v: Math.max(0, margin), color: 'var(--amber)' },
    { v: Math.abs(unrealized), color: unrealized >= 0 ? 'var(--long)' : 'var(--short)' },
  ];
  const total = parts.reduce((s, p) => s + p.v, 0);
  const r = 52, C = 2 * Math.PI * r;
  let offset = 0;
  return (
    <svg viewBox="0 0 120 120">
      <circle cx="60" cy="60" r={r} fill="none" stroke="var(--s3)" strokeWidth="14" />
      {total > 0 && parts.map((p, i) => {
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
  const pos = RISK_POS[level] ?? 0.16;
  const [nx, ny] = polar(100, 100, 74, 180 + pos * 180);
  const arc = (from: number, to: number) => {
    const [x1, y1] = polar(100, 100, 88, from);
    const [x2, y2] = polar(100, 100, 88, to);
    return `M ${x1} ${y1} A 88 88 0 0 1 ${x2} ${y2}`;
  };
  return (
    <svg viewBox="0 0 200 110">
      <path d={arc(180, 240)} fill="none" stroke="var(--long)" strokeWidth="12" strokeLinecap="round" opacity=".85" />
      <path d={arc(240, 300)} fill="none" stroke="var(--amber)" strokeWidth="12" opacity=".85" />
      <path d={arc(300, 360)} fill="none" stroke="var(--short)" strokeWidth="12" strokeLinecap="round" opacity=".85" />
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
      {pct != null && <circle cx="40" cy="40" r={r} fill="none" stroke="var(--accent)" strokeWidth="8"
        strokeLinecap="round" strokeDasharray={`${frac * C} ${C}`} />}
    </svg>
  );
}

type DayPoint = { date: string; pnl: number; trades: number; equity: number | null };

/**
 * Where a number came from (2026-10-09). The owner asked for this on every
 * figure, because "Portfolio says one thing and Perpl says another" is only
 * answerable if each number names its own source.
 */
const SRC_TEXT: Record<string, string> = {
  leaderboard: "Perpl leaderboard: the public all-time figures for this wallet, the same source the Wallet Explorer shows. No one-click key needed.",
  stream: "Perpl account stream (mt:19 `sts`): the venue's own lifetime totals for your account. One frame, complete, not paged.",
  positions: "Perpl position history (/v1/trading/position-history): the venue's own record of each closed position: entry, exit, realized P/L, funding, fees, leverage.",
  fills: "Perpl fills (/v1/trading/fills): every execution, with the real fee Perpl charged.",
  events: "Perpl account history (/v1/trading/account-history): deposits, withdrawals, settlements, funding and liquidations.",
  chain: "Monad chain read (getAccountByAddr / getPosition), uncached, cross-checked against Perpl's account frame.",
  venue: "Perpl's own order/position frames on the trading socket, trigger orders are held by Perpl, not on-chain.",
  ours: "SMINDEX records, which of your orders we placed, used only to label a trade's source.",
};

function Src({ k }: { k: keyof typeof SRC_TEXT }) {
  const label = k === 'leaderboard' ? 'Perpl leaderboard'
    : k === 'stream' ? 'Perpl account'
      : k === 'positions' ? 'Perpl positions'
        : k === 'fills' ? 'Perpl fills'
          : k === 'events' ? 'Perpl transfers'
            : k === 'chain' ? 'chain' : k === 'ours' ? 'SMINDEX' : 'Perpl';
  return <em className="srch" title={SRC_TEXT[k]}>{label}</em>;
}

const PILLS = ['positions', 'orders', 'trades', 'funding', 'transfers', 'copyh', 'copyp'] as const;
type Pill = typeof PILLS[number];
const PILL_LABEL: Record<Pill, string> = {
  positions: 'Positions', orders: 'Open orders', trades: 'Trade history',
  funding: 'Funding', transfers: 'Deposits & withdrawals',
  copyh: 'Copy history', copyp: 'Copy performance',
};
type SourceFilter = 'all' | 'terminal' | 'copy_trade' | 'auto_copy' | 'other';

export default function PortfolioB() {
  const { address, hydrated } = useAuth();
  const account = usePerplAccount(address);
  const copies = useCopyStore((s) => s.copies);
  const toast = useToast();
  const { signMessageAsync } = useSignMessage();
  const [dailyDays, setDailyDays] = useState(14);
  const [grain, setGrain] = useState<Grain>('day');
  const [pill, setPill] = useState<Pill>('positions');
  const [source, setSource] = useState<SourceFilter>('all');
  const [slTpFor, setSlTpFor] = useState<number | null>(null);
  const [closing, setClosing] = useState<number | null>(null);

  // Open the venue socket on arrival, the way the Terminal does (2026-10-09).
  // Without it /portfolio never received mt:19, so the venue's own lifetime
  // totals (volume, fees, realized P/L, deposits, trades, win rate) and the
  // armed SL/TP were blank on this page even with a one-click key enrolled.
  // This is the no-popup path: the enrolled key signs itself.
  useEffect(() => { if (address) warmupTradingConnection(address); }, [address]);

  const keyPresent = hasPerplKey(address);
  // PUBLIC lifetime figures: no key, shown for everyone. Same source as the
  // Wallet Explorer (Perpl's leaderboard API behind our 30s cache), so the two
  // pages cannot disagree.
  const publicQ = useQuery({
    queryKey: ['perpl-public-lifetime', address],
    queryFn: () => api.get(`/api/explorer/perpl/${address}`).then((r) => r.data),
    enabled: !!address, staleTime: 60_000, retry: 1,
  });
  const lifetimePublic = useMemo(() => {
    const ranks = publicQ.data?.leaderboard?.ranks ?? {};
    const pnl = ranks.all_pnl ?? null;
    const vol = ranks.all_vol ?? null;
    return {
      pnl: pnl?.pnl ?? vol?.pnl ?? null,
      roi: pnl?.roi ?? vol?.roi ?? null,
      volume: vol?.volume ?? pnl?.volume ?? null,
      rank: pnl?.rank ?? null,
      volRank: vol?.rank ?? null,
      onLeaderboard: !!publicQ.data?.leaderboard?.on_leaderboard,
    };
  }, [publicQ.data]);

  const fillsQ = useQuery({
    queryKey: ['perpl-fills', address],
    queryFn: () => pageAll((page) => getPerplFills(address!, page), 2000),
    enabled: !!address && keyPresent, staleTime: 60_000, retry: false,
  });
  const eventsQ = useQuery({
    queryKey: ['perpl-account-history', address],
    queryFn: () => pageAll((page) => getPerplAccountHistory(address!, page), 2000),
    enabled: !!address && keyPresent, staleTime: 60_000, retry: false,
  });
  // The venue's OWN per-trade record. This, not FIFO matching of fills, is the
  // trade list: each row already carries entry, exit, realized P/L, funding,
  // fees and leverage as Perpl computed them.
  const posHistQ = useQuery({
    queryKey: ['perpl-position-history', address],
    queryFn: () => pageAll((page) => getPerplPositionHistory(address!, page), 2000),
    enabled: !!address && keyPresent, staleTime: 60_000, retry: false,
  });
  // A fill or an account event on the trading socket re-reads the venue's
  // history (2026-10-22), shortly after so Perpl's own record has caught up;
  // one re-read per burst of frames.
  const qc = useQueryClient();
  useEffect(() => {
    if (!address) return;
    let fillsT: number | undefined;
    let acctT: number | undefined;
    const onVenue = (e: Event) => {
      const d: any = (e as CustomEvent)?.detail?.detail;
      if (Array.isArray(d?.fills) || Array.isArray(d?.positions)) {
        window.clearTimeout(fillsT);
        fillsT = window.setTimeout(() => {
          qc.invalidateQueries({ queryKey: ['perpl-fills', address] });
          qc.invalidateQueries({ queryKey: ['perpl-position-history', address] });
        }, 1500);
      }
      if (d?.account || Array.isArray(d?.fills)) {
        window.clearTimeout(acctT);
        acctT = window.setTimeout(() => qc.invalidateQueries({ queryKey: ['perpl-account-history', address] }), 1500);
      }
    };
    window.addEventListener('venue_account_event', onVenue);
    window.addEventListener('trigger_leg_filled', onVenue);
    return () => {
      window.removeEventListener('venue_account_event', onVenue);
      window.removeEventListener('trigger_leg_filled', onVenue);
      window.clearTimeout(fillsT);
      window.clearTimeout(acctT);
    };
  }, [address, qc]);
  const ourOrdersQ = useQuery({
    queryKey: ['order-history-portfolio'], queryFn: () => getOrderHistory({ limit: 200 }),
    enabled: !!address, staleTime: 60_000,
  });
  const perf = useQuery({ queryKey: ['copy-performance'], queryFn: getCopyPerformance, enabled: !!address });

  const ledger = useMemo(() => buildLedger((fillsQ.data?.rows ?? []) as any), [fillsQ.data]);
  const events = useMemo(() => buildEvents(eventsQ.data?.rows ?? []), [eventsQ.data]);

  // ---- the venue's own records -------------------------------------------
  const vEvents = useMemo(() => buildAccountEvents(eventsQ.data?.rows ?? []), [eventsQ.data]);
  const vTransfers = useMemo(() => transferTotals(vEvents), [vEvents]);
  const vFills = useMemo(() => fillTotals(fillsQ.data?.rows ?? []), [fillsQ.data]);
  const closedTrades = useMemo(
    () => attachHoldTimes(buildClosedTrades(posHistQ.data?.rows ?? []), fillsQ.data?.rows ?? []),
    [posHistQ.data, fillsQ.data],
  );
  const lifetime = account.lifetime;          // mt:19 `sts`, exact and unpaged
  const vStats = useMemo(() => tradeStats(closedTrades), [closedTrades]);
  const perMarket = useMemo(() => byMarket(closedTrades), [closedTrades]);
  const periods = useMemo(() => pnlByPeriod(closedTrades, grain), [closedTrades, grain]);
  const fundingPaid = useMemo(
    () => closedTrades.filter((t) => t.funding < 0).reduce((a, t) => a - t.funding, 0), [closedTrades]);
  const fundingReceived = useMemo(
    () => closedTrades.filter((t) => t.funding > 0).reduce((a, t) => a + t.funding, 0), [closedTrades]);
  const totalSpent = (lifetime?.fees ?? vStats.fees) + fundingPaid;
  // How much history we actually hold, so no figure claims to be "all time"
  // when it is really "the newest N records".
  const coverage = useMemo(() => {
    const complete = !!(fillsQ.data?.complete && posHistQ.data?.complete && eventsQ.data?.complete);
    const from = [vFills.covers.from, vTransfers.covers.from,
      closedTrades.length ? closedTrades[closedTrades.length - 1].at : null]
      .filter((x): x is number => !!x);
    return {
      complete,
      from: from.length ? Math.min(...from) : null,
      rows: (fillsQ.data?.rows?.length ?? 0) + (posHistQ.data?.rows?.length ?? 0) + (eventsQ.data?.rows?.length ?? 0),
    };
  }, [fillsQ.data, posHistQ.data, eventsQ.data, vFills, vTransfers, closedTrades]);
  const sourceByOid = useMemo(() => {
    const m = new Map<string, string>();
    (ourOrdersQ.data ?? []).forEach((o: any) => { if (o.order_id) m.set(String(o.order_id), o.source || 'manual'); });
    return m;
  }, [ourOrdersQ.data]);
  const tradeSource = (t: LedgerTrade): string =>
    sourceByOid.get(String(t.closeOid)) ?? t.openOids.map((o) => sourceByOid.get(String(o))).find(Boolean) ?? 'other';

  const shownTrades = useMemo(() => (source === 'all' ? ledger.trades : ledger.trades.filter((t) => {
    const s = tradeSource(t);
    if (source === 'terminal') return s === 'manual' || s === 'terminal';
    if (source === 'other') return !['manual', 'terminal', 'copy_trade', 'auto_copy'].includes(s);
    return s === source;
  })), [ledger.trades, source, sourceByOid]);
  const rec = useMemo(() => totalsFor(shownTrades), [shownTrades]);

  // Realized P/L from the venue's own position records (not FIFO estimates).
  const realizedToday = useMemo(
    () => realizedSinceTrades(closedTrades, startOfUtcToday()), [closedTrades]);
  const realized7d = useMemo(
    () => realizedSinceTrades(closedTrades, Date.now() - 7 * 86400_000), [closedTrades]);
  const realized30d = useMemo(
    () => realizedSinceTrades(closedTrades, Date.now() - 30 * 86400_000), [closedTrades]);
  const funding = useMemo(() => {
    const m = events.events.filter((e) => e.kind === 'market');
    return {
      rows: m,
      received: m.filter((e) => e.amount > 0).reduce((a, e) => a + e.amount, 0),
      paid: m.filter((e) => e.amount < 0).reduce((a, e) => a + Math.abs(e.amount), 0),
    };
  }, [events]);
  const transfers = useMemo(() => events.events.filter((e) => e.kind === 'transfer'), [events]);

  // Daily series: realized P/L per day from real fills, with the account value
  // recorded by Perpl's own events as the dots.
  const daily: DayPoint[] = useMemo(() => {
    const byDay = dailyPnl(ledger.trades);
    const eqByDay = new Map<string, number>();
    events.events.forEach((e) => {
      if (e.balanceAfter == null) return;
      const d = new Date(e.at).toISOString().slice(0, 10);
      if (!eqByDay.has(d)) eqByDay.set(d, (e.balanceAfter ?? 0) + (e.lockedAfter ?? 0));
    });
    const out: DayPoint[] = [];
    for (let i = dailyDays - 1; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400_000).toISOString().slice(0, 10);
      const hit = byDay.get(d);
      out.push({ date: d, pnl: hit?.pnl ?? 0, trades: hit?.trades ?? 0, equity: eqByDay.get(d) ?? null });
    }
    return out;
  }, [ledger.trades, events, dailyDays]);
  const hasDaily = daily.some((d) => d.trades > 0 || d.equity != null);

  // Risk, leverage and stress straight from the positions we are showing.
  const totalNotional = account.positions.reduce((a, p) => a + (p.notional || 0), 0);
  const closest = account.positions.reduce<null | { pct: number; symbol: string }>((best, p) => (
    p.liqDistancePct == null ? best
      : !best || p.liqDistancePct < best.pct ? { pct: p.liqDistancePct, symbol: p.symbol } : best
  ), null);
  const riskLevel = account.positions.length === 0 ? 'safe'
    : closest == null ? 'safe'
      : closest.pct < 5 ? 'critical' : closest.pct < 10 ? 'danger' : closest.pct < 20 ? 'warning' : 'safe';
  const accountLeverage = account.accountValue > 0 ? totalNotional / account.accountValue : 0;
  const marginUsagePct = account.accountValue > 0 ? (account.marginInUse / account.accountValue) * 100 : 0;
  const withSl = account.positions.filter((p) => triggersFor(account, p.marketId, p.side).sl).length;
  const withTp = account.positions.filter((p) => triggersFor(account, p.marketId, p.side).tp).length;

  const pipRow = (covered: number, open: number) => (
    <div className="pips">{Array.from({ length: 5 }).map((_, i) => (
      <i key={i} className={open > 0 && i < Math.round((covered / open) * 5) ? 'on' : ''} />
    ))}</div>
  );

  const ensureConnected = async (): Promise<boolean> => {
    const sess = getSession();
    if (sess.authenticated && sess.hasWs) return true;
    if (!address) return false;
    try {
      if (await tryAutoReconnect(address)) return true;
      const payload = await getPerplPayload(address);
      const signature = await signMessageAsync({ message: payload.message });
      const nonce = await connectPerpl(address, payload, signature);
      await openTradingWs(nonce, address);
      if (account.accountId) setAccountId(account.accountId);
      return true;
    } catch (e: any) {
      toast.error(e?.message || 'Failed to connect to Perpl');
      return false;
    }
  };

  const handleClose = (p: typeof account.positions[number]) => oneAtATime(`close:${p.marketId}`, () => handleCloseOnce(p));
  const handleCloseOnce = async (p: typeof account.positions[number]) => {
    if (!(await ensureConnected())) return;
    let mcfg;
    try {
      mcfg = requireMarketConfig(p.marketId);
    } catch {
      toast.error('Market settings not loaded. Refresh and try again.');
      return;
    }
    setClosing(p.marketId);
    try {
      await closePosition({
        marketId: p.marketId, side: p.side, size: p.size,
        priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals, markPrice: p.markPrice,
      });
      toast.success(`Closed ${p.symbol} ${p.side}`);
      window.dispatchEvent(new CustomEvent('position_closed'));
    } catch (e: any) {
      toast.error(e?.message || 'Close failed');
    } finally {
      setClosing(null);
    }
  };

  const exportPnl = () => {
    api.get('/api/export/pnl', { responseType: 'blob' }).then((res) => {
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `pnl_${address?.slice(0, 10)}_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      window.URL.revokeObjectURL(url);
    });
  };

  if (!hydrated) return <div className="screen"><div className="empty">Loading…</div></div>;
  if (!address) {
    return (
      <div className="screen">
        <div className="head"><div><h1>Portfolio</h1><p>Connect your wallet to view your portfolio</p></div></div>
        <div className="empty">Connect your wallet to view portfolio.</div>
      </div>
    );
  }
  // Only ever a first-load state: once anything has loaded, the numbers stay on
  // screen and are updated in place (no skeleton on a background refresh).
  if (!hasAccountData(account) && account.loading) {
    return <div className="screen"><div className="head"><div><h1>Portfolio</h1></div></div><div className="empty">Reading your account from Perpl…</div></div>;
  }

  const perfRows: any[] = perf.data || [];
  const maxAbs = Math.max(1, ...daily.map((d) => Math.abs(d.pnl)));
  const eqs = daily.map((d) => d.equity).filter((v): v is number => v != null);
  const eqMin = Math.min(...eqs, 0), eqMax = Math.max(...eqs, 1);
  const dec = (mid: number) => MARKETS[mid]?.decimals ?? 2;
  const perplBlocked = !keyPresent || !!fillsQ.error;
  const perplBlockedMsg = !keyPresent
    ? 'Enable one-click trading in Settings and your Perpl fills, fees, funding and transfers appear here. No wallet signature is used for reading.'
    : `Perpl history read failed: ${(fillsQ.error as any)?.response?.data?.detail || (fillsQ.error as any)?.message || 'unknown error'}`;

  return (
    <div className="screen">
      <div className="head">
        <div>
          <h1>Portfolio</h1>
          <p>
            <span className="mono">{shortenAddress(address)}</span> on Perpl, Monad
            {account.accountId ? <> · account <span className="mono">#{account.accountId}</span></> : null}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {/* A dot with an age, not a spinner: background refreshes never blank
              the page, so there is nothing to show a loading state for. */}
          <LiveDot account={account} />
          <button className="btn sm" onClick={exportPnl}>Export PnL</button>
        </div>
      </div>

      {/* Lifetime, PUBLIC: no one-click key, shown for every wallet. */}
      <div className="card lifetime" style={{ marginBottom: 12 }}>
        <div className="lfhead">
          <div>
            <h2>All time on Perpl</h2>
            <div className="sub" style={{ margin: 0 }}>
              Public figures for this wallet · <Src k="leaderboard" />
            </div>
          </div>
          {lifetimePublic.rank != null && (
            <span className="pill">Rank #{lifetimePublic.rank}{lifetimePublic.volRank != null ? ` · #${lifetimePublic.volRank} by volume` : ''}</span>
          )}
        </div>
        <div className="lfgrid">
          <div>
            <span>Total P/L <Src k="leaderboard" /></span>
            <b className={(lifetimePublic.pnl ?? 0) >= 0 ? 'long-c' : 'short-c'}>
              {lifetimePublic.pnl == null ? '-'
                : `${lifetimePublic.pnl >= 0 ? '+' : ''}${formatUSD(lifetimePublic.pnl)}`}
            </b>
          </div>
          <div>
            <span>Total volume <Src k="leaderboard" /></span>
            <b>{lifetimePublic.volume == null ? '-' : formatUSD(lifetimePublic.volume)}</b>
          </div>
          <div>
            <span>ROI <Src k="leaderboard" /></span>
            <b className={(lifetimePublic.roi ?? 0) >= 0 ? 'long-c' : 'short-c'}>
              {lifetimePublic.roi == null ? '-' : `${lifetimePublic.roi.toFixed(1)}%`}
            </b>
          </div>
          <div>
            <span>Trades <Src k="stream" /></span>
            <b>{lifetime ? lifetime.trades.toLocaleString() : '-'}</b>
          </div>
          <div>
            <span>Win rate <Src k="stream" /></span>
            <b>{lifetime ? `${lifetime.winRatePct.toFixed(2)}%` : '-'}</b>
          </div>
          <div>
            <span>Fees paid <Src k="stream" /></span>
            <b>{lifetime ? formatUSD(lifetime.fees) : '-'}</b>
          </div>
        </div>
        {!lifetimePublic.onLeaderboard && !lifetime && (
          <div className="sub" style={{ marginTop: 8 }}>
            This wallet is not in Perpl's public leaderboard windows. Connect to Perpl and the
            venue's own lifetime totals for your account appear here.
          </div>
        )}
        {lifetime && (
          <div className="lfgrid" style={{ marginTop: 8 }}>
            <div>
              <span>Volume <Src k="stream" /></span>
              <b>{formatUSD(lifetime.volume)}</b>
            </div>
            <div>
              <span>Realized P/L <Src k="stream" /></span>
              <b className={lifetime.realizedPnl >= 0 ? 'long-c' : 'short-c'}>
                {lifetime.realizedPnl >= 0 ? '+' : ''}{formatUSD(lifetime.realizedPnl)}
              </b>
            </div>
            <div>
              <span>Deposited <Src k="stream" /></span>
              <b>{formatUSD(lifetime.deposited)}</b>
            </div>
            <div>
              <span>Withdrawn <Src k="stream" /></span>
              <b>{formatUSD(lifetime.withdrawn)}</b>
            </div>
            <div>
              <span>Net deposited <Src k="stream" /></span>
              <b>{formatUSD(lifetime.deposited - lifetime.withdrawn)}</b>
            </div>
            <div>
              <span>Total spent <Src k="positions" /></span>
              <b className="short-c">{formatUSD(totalSpent)}</b>
            </div>
          </div>
        )}
      </div>

      {!account.accountId && (
        <div className="empty" style={{ marginBottom: 12, textAlign: 'left' }}>
          No Perpl exchange account found for this wallet yet. Your copied-trade record still appears below.
        </div>
      )}
      {account.error && (
        <div className="empty" style={{ marginBottom: 12, textAlign: 'left' }}>
          Last account read failed ({account.error}): the numbers below are from {new Date(account.at).toLocaleTimeString()}.
        </div>
      )}
      {account.venueAccountMismatch && (
        <div className="empty" style={{ marginBottom: 12, textAlign: 'left' }}>
          Perpl's account frame and the chain read disagree: {account.venueAccountMismatch}. Showing the chain read.
        </div>
      )}

      {/* Row 1: bento */}
      <div className="bento">
        <div className="card bal">
          <div className="ring">
            <AccountRing available={account.available} margin={account.marginInUse} unrealized={account.unrealizedPnl} />
            <div className="ringc"><div className="v">{formatUSD(account.accountValue)}</div><div className="l">Account value</div></div>
          </div>
          <div className="ringlegend">
            <div><i style={{ background: 'var(--accent)' }} />Available<b>{formatUSD(account.available)}</b></div>
            <div><i style={{ background: 'var(--amber)' }} />Margin in use<b>{formatUSD(account.marginInUse)}</b></div>
            <div><i style={{ background: account.unrealizedPnl >= 0 ? 'var(--long)' : 'var(--short)' }} />Unrealized<b className={account.unrealizedPnl >= 0 ? 'long-c' : 'short-c'}>{account.unrealizedPnl >= 0 ? '+' : ''}{formatUSD(account.unrealizedPnl)}</b></div>
          </div>
          <div className="stress" style={{ marginTop: 10 }}>
            <div><span>Available <Src k="chain" /></span><b>{formatUSD(account.available)}</b></div>
            <div><span>Margin in use <Src k="chain" /></span><b>{formatUSD(account.marginInUse)}</b></div>
            <div><span>Realized today <Src k="positions" /></span><b className={realizedToday >= 0 ? 'long-c' : 'short-c'}>{perplBlocked ? '-' : `${realizedToday >= 0 ? '+' : ''}${formatUSD(realizedToday)}`}</b></div>
            <div><span>Realized 7 days <Src k="positions" /></span><b className={realized7d >= 0 ? 'long-c' : 'short-c'}>{perplBlocked ? '-' : `${realized7d >= 0 ? '+' : ''}${formatUSD(realized7d)}`}</b></div>
            <div><span>Realized 30 days <Src k="positions" /></span><b className={realized30d >= 0 ? 'long-c' : 'short-c'}>{perplBlocked ? '-' : `${realized30d >= 0 ? '+' : ''}${formatUSD(realized30d)}`}</b></div>
            <div><span>Realized all time <Src k="stream" /></span><b className={(lifetime?.realizedPnl ?? 0) >= 0 ? 'long-c' : 'short-c'}>{lifetime ? `${lifetime.realizedPnl >= 0 ? '+' : ''}${formatUSD(lifetime.realizedPnl)}` : '-'}</b></div>
            <div><span>Fees paid <Src k="stream" /></span><b>{lifetime ? formatUSD(lifetime.fees) : (perplBlocked ? '-' : formatUSD(vFills.fees))}</b></div>
            <div><span>Funding received <Src k="positions" /></span><b className="long-c">{perplBlocked ? '-' : `+${formatUSD(fundingReceived)}`}</b></div>
            <div><span>Funding paid <Src k="positions" /></span><b className="short-c">{perplBlocked ? '-' : `-${formatUSD(fundingPaid)}`}</b></div>
            <div><span>Total spent (fees + funding paid) <Src k="positions" /></span><b className="short-c">{perplBlocked && !lifetime ? '-' : formatUSD(totalSpent)}</b></div>
            {vTransfers.liquidations > 0 && (
              <div><span>Liquidations <Src k="events" /></span><b className="short-c">{vTransfers.liquidations}</b></div>
            )}
          </div>
        </div>

        <div className="card">
          <h2>Risk right now</h2>
          <div className="sub">How close open positions are to liquidation</div>
          <div className="gaugewrap">
            <RiskGauge level={riskLevel} />
            <div className="gaugetxt">
              <b className={RISK_CLS[riskLevel]}>{RISK_WORD[riskLevel]}</b>
              <span>{account.positions.length === 0 ? 'No open positions'
                : closest ? `Closest liquidation ${closest.pct.toFixed(1)}% away (${closest.symbol})`
                  : `${account.positions.length} open`}</span>
            </div>
          </div>
          <div className="stress">
            <div><span>Leverage</span><b>{accountLeverage.toFixed(2)}x</b></div>
            <div><span>Margin usage</span><b>{marginUsagePct.toFixed(2)}%</b></div>
            <div><span>5% move against you</span><b className={totalNotional > 0 ? 'short-c' : 'dim'}>{totalNotional > 0 ? `-${formatUSD(totalNotional * 0.05)}` : 'No impact'}</b></div>
            <div><span>10% move against you</span><b className={totalNotional > 0 ? 'short-c' : 'dim'}>{totalNotional > 0 ? `-${formatUSD(totalNotional * 0.10)}` : 'No impact'}</b></div>
          </div>
          <div className="cover"><span>Stop loss cover</span>{pipRow(withSl, account.positions.length)}<b>{withSl} of {account.positions.length}</b></div>
          <div className="cover"><span>Take profit cover</span>{pipRow(withTp, account.positions.length)}<b>{withTp} of {account.positions.length}</b></div>
          {!account.venueSession && account.positions.length > 0 && (
            <div className="sub" style={{ marginTop: 6 }}>
              SL/TP cover needs the Perpl connection (trigger orders are held by Perpl, not on-chain).
            </div>
          )}
        </div>

        <div className="card">
          <h2>Trading record</h2>
          <div className="sub">
            Every closed trade on this account, matched from Perpl's own fills
            {source !== 'all' ? ` · ${SOURCE_LABEL[source]} only` : ''}
          </div>
          <div className="seg" style={{ margin: '8px 0', flexWrap: 'wrap' }}>
            {(['all', 'terminal', 'copy_trade', 'auto_copy', 'other'] as SourceFilter[]).map((f) => (
              <button key={f} aria-pressed={source === f} onClick={() => setSource(f)}>
                {f === 'all' ? 'All' : SOURCE_LABEL[f]}
              </button>
            ))}
          </div>
          {perplBlocked ? (
            <div className="empty" style={{ marginTop: 12 }}>{perplBlockedMsg}</div>
          ) : posHistQ.isLoading && !closedTrades.length ? (
            <div className="empty" style={{ marginTop: 12 }}>Reading your trades from Perpl…</div>
          ) : (source === 'all' ? vStats.trades : rec.trades) === 0 ? (
            <div className="empty" style={{ marginTop: 12 }}>
              {closedTrades.length === 0
                ? 'Perpl reports no closed positions for this account yet.'
                : 'No closed trades for this source yet.'}
            </div>
          ) : source !== 'all' ? (
            // A source filter is only knowable from OUR order records, which
            // exist per fill, so a filtered view stays on the FIFO ledger and
            // says so, rather than silently mixing two definitions.
            <div className="recgrid">
              <div className="wr"><WinRing pct={rec.winRatePct} /><div><b>{rec.winRatePct.toFixed(1)}%</b><span>Win rate</span></div></div>
              <div className="rkv"><b>{rec.trades.toLocaleString()}</b><span>Closed trades</span></div>
              <div className="rkv"><b>{formatUSD(rec.volumeUsd)}</b><span>Volume</span></div>
              <div className="rkv"><b className={rec.netPnl >= 0 ? 'long-c' : 'short-c'}>{rec.netPnl >= 0 ? '+' : ''}{formatUSD(rec.netPnl)}</b><span>Net result</span></div>
              <div className="rkv"><b className={rec.netPnl >= 0 ? 'long-c' : 'short-c'}>{`${rec.netPnl >= 0 ? '+' : ''}${formatUSD(rec.netPnl / (rec.trades || 1))}`}</b><span>Per trade</span></div>
              <div className="rkv"><b>{formatUSD(rec.feesUsd)}</b><span>Fees <Src k="fills" /></span></div>
            </div>
          ) : (
            <div className="recgrid">
              <div className="wr">
                <WinRing pct={vStats.winRateGrossPct} />
                <div>
                  <b title={`${vStats.winRateGrossPct?.toFixed(2) ?? '-'}% of closed positions had a positive realized P/L. After that position's own fees and funding, ${vStats.winRatePct?.toFixed(2) ?? '-'}% were still positive.${lifetime ? ` Perpl's own lifetime win rate for this account is ${lifetime.winRatePct.toFixed(2)}%.` : ''}`}>
                    {vStats.winRateGrossPct == null ? '-' : `${vStats.winRateGrossPct.toFixed(1)}%`}
                  </b>
                  <span>Win rate <Src k="positions" /></span>
                  {vStats.winRatePct != null && (
                    <span className="dim" style={{ fontSize: 11 }}>{vStats.winRatePct.toFixed(1)}% after fees</span>
                  )}
                </div>
              </div>
              <div className="rkv"><b>{vStats.trades.toLocaleString()}</b><span>Closed trades</span></div>
              <div className="rkv"><b>{formatUSD(vStats.volume)}</b><span>Volume</span></div>
              <div className="rkv"><b className={vStats.netPnl >= 0 ? 'long-c' : 'short-c'}>{vStats.netPnl >= 0 ? '+' : ''}{formatUSD(vStats.netPnl)}</b><span>Net result</span></div>
              <div className="rkv"><b className={(vStats.avgTrade ?? 0) >= 0 ? 'long-c' : 'short-c'}>{vStats.avgTrade == null ? '-' : `${vStats.avgTrade >= 0 ? '+' : ''}${formatUSD(vStats.avgTrade)}`}</b><span>Per trade</span></div>
              <div className="rkv"><b className="long-c">{vStats.best ? `+${formatUSD(vStats.best.netPnl)}` : '-'}</b><span>Best{vStats.best ? ` · ${vStats.best.symbol}` : ''}</span></div>
              <div className="rkv"><b className="short-c">{vStats.worst ? formatUSD(vStats.worst.netPnl) : '-'}</b><span>Worst{vStats.worst ? ` · ${vStats.worst.symbol}` : ''}</span></div>
              <div className="rkv"><b>{formatHold(vStats.avgHoldMs)}</b><span>Average hold <Src k="fills" /></span></div>
              <div className="rkv"><b>{vStats.avgLeverage == null ? '-' : `${vStats.avgLeverage.toFixed(1)}x`}</b><span>Average leverage</span></div>
            </div>
          )}
          {!perplBlocked && vFills.skippedUnknownMarket > 0 && (
            <div className="sub" style={{ marginTop: 8 }}>
              {vFills.skippedUnknownMarket} fill{vFills.skippedUnknownMarket > 1 ? 's' : ''} are on a market Perpl no longer
              lists, so its decimals are unknown and its notional is left out of the volume above rather than guessed.
              Fees on those fills ARE counted.
            </div>
          )}
          {!perplBlocked && closedTrades.length > 0 && (
            <div className="sub" style={{ marginTop: 8 }}>
              {coverage.complete
                ? `Complete history: ${coverage.rows.toLocaleString()} records read from Perpl.`
                : `Covers ${coverage.from ? new Date(coverage.from).toISOString().slice(0, 10) : '-'} to today (${coverage.rows.toLocaleString()} records). All-time totals above come from Perpl's own account figures, which are complete.`}
            </div>
          )}
        </div>
      </div>

      {/* Row 2: daily result + calendar, both from real fills */}
      <div className="row two" style={{ marginTop: 12 }}>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
            <div>
              <h2>Result by {grain}</h2>
              <div className="sub" style={{ margin: 0 }}>
                Realized P/L per {grain} from Perpl's own closed positions <Src k="positions" />
                {grain === 'day' ? ' · dots are the account value Perpl recorded' : ''}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <div className="seg">{(['day', 'week', 'month'] as Grain[]).map((g) => (
                <button key={g} aria-pressed={grain === g} onClick={() => setGrain(g)}>
                  {g === 'day' ? 'Day' : g === 'week' ? 'Week' : 'Month'}
                </button>
              ))}</div>
              {grain === 'day' && (
                <div className="seg">{[['7d', 7], ['14d', 14], ['30d', 30], ['90d', 90]].map(([l, d]) => (
                  <button key={d as number} aria-pressed={dailyDays === d} onClick={() => setDailyDays(d as number)}>{l}</button>
                ))}</div>
              )}
            </div>
          </div>
          {perplBlocked ? <div className="empty">{perplBlockedMsg}</div>
            : posHistQ.isLoading && !closedTrades.length ? <div className="empty">Reading your trades from Perpl…</div>
              : grain !== 'day' ? (
                periods.length === 0 ? <div className="empty">No closed trades yet.</div> : (
                  <>
                    <svg viewBox="0 0 600 200" style={{ width: '100%', height: 200, display: 'block' }}>
                      {(() => {
                        const rows = periods.slice(0, grain === 'week' ? 26 : 18).reverse();
                        const max = Math.max(1, ...rows.map((r) => Math.abs(r.pnl)));
                        return rows.map((r, i) => {
                          const bw = 600 / rows.length; const x = i * bw + bw * 0.2; const w = bw * 0.6;
                          const h = (Math.abs(r.pnl) / max) * 80;
                          const y = r.pnl >= 0 ? 100 - h : 100;
                          return (
                            <rect key={r.key} x={x} y={y} width={w} height={Math.max(r.pnl === 0 ? 0 : 1, h)}
                              fill={r.pnl > 0 ? 'var(--long)' : r.pnl < 0 ? 'var(--short)' : 'var(--dim)'} rx="2">
                              <title>{`${r.label}: ${r.pnl >= 0 ? '+' : ''}${r.pnl.toFixed(2)} over ${r.trades} trades`}</title>
                            </rect>
                          );
                        });
                      })()}
                      <line x1="0" y1="100" x2="600" y2="100" stroke="var(--line)" strokeWidth="1" />
                    </svg>
                    <div className="tblwrap" style={{ marginTop: 8, maxHeight: 220, overflow: 'auto' }}>
                      <table className="tbl">
                        <thead>
                          <tr>
                            <th>{grain === 'week' ? 'Week' : 'Month'}</th>
                            <th className="r">Realized P/L</th><th className="r">Trades</th>
                            <th className="r">Volume</th><th className="r">Fees</th><th className="r">Funding</th>
                          </tr>
                        </thead>
                        <tbody>
                          {periods.map((r) => (
                            <tr key={r.key}>
                              <td>{r.label}</td>
                              <td className={`r ${r.pnl >= 0 ? 'long-c' : 'short-c'}`}>{r.pnl >= 0 ? '+' : ''}{formatUSD(r.pnl)}</td>
                              <td className="r">{r.trades}</td>
                              <td className="r">{formatUSD(r.volume)}</td>
                              <td className="r">{formatUSD(r.fees)}</td>
                              <td className={`r ${r.funding >= 0 ? 'long-c' : 'short-c'}`}>{r.funding >= 0 ? '+' : ''}{formatUSD(r.funding)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )
              )
              : !hasDaily ? <div className="empty">No closed trades or account events in this window.</div>
                : (
                  <svg viewBox="0 0 600 220" style={{ width: '100%', height: 220, display: 'block' }}>
                    {daily.map((d, i) => {
                      const bw = 600 / daily.length, x = i * bw + bw * 0.2, w = bw * 0.6;
                      const h = (Math.abs(d.pnl) / maxAbs) * 80;
                      const y = d.pnl >= 0 ? 110 - h : 110;
                      const ey = d.equity == null ? null : 200 - ((d.equity - eqMin) / (eqMax - eqMin || 1)) * 180;
                      return (
                        <g key={i}>
                          <rect x={x} y={y} width={w} height={Math.max(d.pnl === 0 ? 0 : 1, h)}
                            fill={d.pnl > 0 ? 'var(--long)' : d.pnl < 0 ? 'var(--short)' : 'var(--dim)'} rx="2" />
                          {ey != null && <circle cx={x + w / 2} cy={ey} r="2" fill="var(--accent)" />}
                        </g>
                      );
                    })}
                    <line x1="0" y1="110" x2="600" y2="110" stroke="var(--line)" strokeWidth="1" />
                  </svg>
                )}
        </div>
        <div className="card">
          <h2>Trading calendar</h2>
          <div className="sub">Last six weeks of realized P/L. Darker green is a bigger up day, red a down day.</div>
          <Calendar series={daily} all={dailyPnl(ledger.trades)} />
          <div className="heatlegend">
            <span>Less</span><i style={{ background: 'var(--s2)' }} /><i style={{ background: 'var(--long-soft)' }} />
            <i style={{ background: 'var(--long)', opacity: .6 }} /><i style={{ background: 'var(--long)' }} /><span>More</span>
            <span style={{ marginLeft: 12 }}><i style={{ background: 'var(--short)', display: 'inline-block', verticalAlign: 'middle' }} /> Loss</span>
          </div>
        </div>
      </div>

      {/* Volume and result per market, from the venue's own closed positions */}
      {!perplBlocked && perMarket.length > 0 && (
        <div className="card" style={{ marginTop: 12 }}>
          <h2>By market</h2>
          <div className="sub">Volume and realized result per market <Src k="positions" /></div>
          <div className="tblwrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Market</th><th className="r">Trades</th><th className="r">Volume</th>
                  <th className="r">Win rate</th><th className="r">Fees</th><th className="r">Net result</th>
                </tr>
              </thead>
              <tbody>
                {perMarket.map((m) => (
                  <tr key={m.marketId}>
                    <td><b>{m.symbol}</b></td>
                    <td className="r">{m.trades.toLocaleString()}</td>
                    <td className="r">{formatUSD(m.volume)}</td>
                    <td className="r">{m.winRatePct == null ? '-' : `${m.winRatePct.toFixed(1)}%`}</td>
                    <td className="r">{formatUSD(m.fees)}</td>
                    <td className={`r ${m.netPnl >= 0 ? 'long-c' : 'short-c'}`}>{m.netPnl >= 0 ? '+' : ''}{formatUSD(m.netPnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Row 3: pills */}
      <div style={{ marginTop: 12 }}>
        <div className="seg" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
          {PILLS.map((p) => (
            <button key={p} aria-pressed={pill === p} onClick={() => setPill(p)}>
              {p === 'positions' ? `Positions (${account.positions.length})`
                : p === 'orders' ? `Open orders (${account.chainOrders.length + account.triggers.length})`
                  : p === 'trades' ? `Trade history (${ledger.trades.length})`
                    : PILL_LABEL[p]}
            </button>
          ))}
        </div>

        {pill === 'positions' && (
          account.positions.length === 0
            ? <div className="empty">No open positions. Anything you open on the terminal or through copy trade appears here with size, entry, mark, result and liquidation distance.</div>
            : <div className="cards">{account.positions.map((p) => {
              const { sl, tp } = triggersFor(account, p.marketId, p.side);
              const d = dec(p.marketId);
              return (
                <div className="tc" key={p.marketId}>
                  <div className="top"><div>
                    <div className="name">{p.symbol} <span className={`tag ${p.side}`}>{p.side === 'long' ? 'Long' : 'Short'} {p.leverage.toFixed(0)}x</span></div>
                    <div className="meta">
                      {p.liqPrice != null ? <>Liq ${formatPrice(p.liqPrice, d)} · {p.liqDistancePct?.toFixed(1)}% away</> : 'Liquidation price needs the market settings'}
                    </div>
                  </div></div>
                  <div className="pnl"><div className="v" style={{ fontSize: 20 }}>
                    <span className={p.pnl >= 0 ? 'long-c' : 'short-c'}>{p.pnl >= 0 ? '+' : ''}{formatUSD(p.pnl)}</span>
                    <small>Unrealized · {p.roePct >= 0 ? '+' : ''}{p.roePct.toFixed(1)}% ROE · funding {p.fundingPnl >= 0 ? '+' : ''}{formatUSD(p.fundingPnl)}</small>
                  </div></div>
                  <div className="facts">
                    <div><b>{p.size}</b><span>Size</span></div>
                    <div><b>{formatUSD(p.deposit)}</b><span>Margin</span></div>
                    <div><b>${formatPrice(p.entryPrice, d)}</b><span>Entry</span></div>
                    <div><b>${formatPrice(p.markPrice, d)}</b><span>Mark</span></div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    <span className="tag flat" style={{ opacity: sl ? 1 : .4 }}>
                      SL {sl ? (triggerPrice(sl) != null ? formatPrice(triggerPrice(sl)!, d) : 'set') : 'none'}
                      {sl && triggerSize(sl) != null && triggerSize(sl)! + 1e-12 < p.size ? ` · ${triggerSize(sl)} of ${p.size}` : ''}
                    </span>
                    <span className="tag flat" style={{ opacity: tp ? 1 : .4 }}>
                      TP {tp ? (triggerPrice(tp) != null ? formatPrice(triggerPrice(tp)!, d) : 'set') : 'none'}
                    </span>
                    <button className="btn sm" onClick={() => setSlTpFor(slTpFor === p.marketId ? null : p.marketId)}>
                      {sl || tp ? 'Edit SL/TP' : 'Add SL/TP'}
                    </button>
                    <button className="btn sm" onClick={() => handleClose(p)} disabled={closing === p.marketId}>
                      {closing === p.marketId ? 'Closing…' : 'Close 100%'}
                    </button>
                  </div>
                  {slTpFor === p.marketId && (
                    <div style={{ marginTop: 10 }}>
                      <SlTpEditor account={account} ensureConnected={ensureConnected}
                        position={{
                          marketId: p.marketId, symbol: p.symbol, side: p.side, size: p.size,
                          markPrice: p.markPrice, leverage: p.leverage,
                        }} />
                    </div>
                  )}
                </div>
              );
            })}</div>
        )}

        {pill === 'orders' && (
          <div style={{ display: 'grid', gap: 10 }}>
            <div className="card" style={{ padding: 14 }}>
              <h2>Trigger orders on Perpl (SL / TP)</h2>
              <div className="sub">Held by Perpl's own order service, not on-chain, this list comes from the venue's order frames.</div>
              {!account.venueSession ? <div className="empty" style={{ marginTop: 10 }}>Connect to Perpl to list your armed SL/TP.</div>
                : account.triggers.length === 0 ? <div className="empty" style={{ marginTop: 10 }}>No armed SL/TP on Perpl.</div>
                  : <div className="ltable" style={{ marginTop: 10 }}><table><thead><tr>
                    <th>Asset</th><th>Kind</th><th>Closes</th><th className="r">Trigger</th><th className="r">Size</th><th className="r">Order</th>
                  </tr></thead><tbody>
                    {account.triggers.map((o) => {
                      const isSl = o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1);
                      const px = triggerPrice(o), sz = triggerSize(o);
                      return (
                        <tr key={o.oid}>
                          <td className="amt">{MARKETS[o.mkt]?.symbol ?? `M${o.mkt}`}</td>
                          <td><span className={`tag ${isSl ? 'short' : 'long'}`}>{isSl ? 'Stop loss' : 'Take profit'}</span></td>
                          <td className="dim">{o.t === 3 ? 'long' : 'short'}{o.st === 9 ? ' · triggered' : ''}</td>
                          <td className="r">{px != null ? `$${formatPrice(px, dec(o.mkt))}` : '-'}</td>
                          <td className="r">{sz != null ? sz : <span className="dim">not reported</span>}</td>
                          <td className="r mono dim">#{o.oid}</td>
                        </tr>
                      );
                    })}
                  </tbody></table></div>}
            </div>
            <div className="card" style={{ padding: 14 }}>
              <h2>Resting orders on-chain</h2>
              <div className="sub">Limit orders sitting in the book, read from the contract.</div>
              {account.chainOrders.length === 0 ? <div className="empty" style={{ marginTop: 10 }}>No resting orders.</div>
                : <div className="ltable" style={{ marginTop: 10 }}><table><thead><tr>
                  <th>Asset</th><th>Type</th><th className="r">Price</th><th className="r">Size</th><th className="r">Margin held</th><th className="r">Order</th>
                </tr></thead><tbody>
                  {account.chainOrders.map((o: any) => (
                    <tr key={`${o.lock_id}-${o.order_id}`}>
                      <td className="amt">{o.symbol}</td>
                      <td className="dim">{String(o.order_type).replace(/_/g, ' ')}</td>
                      <td className="r">{o.price != null ? `$${formatPrice(o.price, dec(o.market_id))}` : '-'}</td>
                      <td className="r">{o.size ?? '-'}</td>
                      <td className="r">{formatUSD(o.margin_locked ?? 0)}</td>
                      <td className="r mono dim">#{o.order_id}</td>
                    </tr>
                  ))}
                </tbody></table></div>}
            </div>
          </div>
        )}

        {pill === 'trades' && (
          perplBlocked ? <div className="empty">{perplBlockedMsg}</div>
            : fillsQ.isLoading ? <div className="empty">Reading your fills from Perpl…</div>
              : shownTrades.length === 0 ? <div className="empty">{ledger.fillCount === 0 ? 'Perpl reports no fills for this account yet.' : 'No closed trades for this source.'}</div>
                : <div className="ltable"><table><thead><tr>
                  <th>Closed</th><th>Asset</th><th>Side</th><th className="r">Size</th><th className="r">Entry</th>
                  <th className="r">Exit</th><th className="r">Fee</th><th className="r">Net result</th><th>Source</th>
                </tr></thead><tbody>
                  {shownTrades.slice(0, 200).map((t) => (
                    <tr key={`${t.closeOid}-${t.closedAt}`}>
                      <td className="dim">{new Date(t.closedAt).toLocaleString()}</td>
                      <td className="amt">{MARKETS[t.marketId]?.symbol ?? `M${t.marketId}`}</td>
                      <td><span className={`tag ${t.side}`}>{t.side === 'long' ? 'Long' : 'Short'}</span></td>
                      <td className="r">{t.size}</td>
                      <td className="r">${formatPrice(t.entryPrice, dec(t.marketId))}</td>
                      <td className="r">${formatPrice(t.exitPrice, dec(t.marketId))}</td>
                      <td className="r dim">{formatUSD(t.fees)}</td>
                      <td className={`r ${t.netPnl >= 0 ? 'long-c' : 'short-c'}`}>{t.netPnl >= 0 ? '+' : ''}{formatUSD(t.netPnl)}</td>
                      <td className="dim">{SOURCE_LABEL[tradeSource(t)] ?? tradeSource(t)}</td>
                    </tr>
                  ))}
                </tbody></table></div>
        )}

        {pill === 'funding' && (
          perplBlocked ? <div className="empty">{perplBlockedMsg}</div>
            : funding.rows.length === 0 ? <div className="empty">Perpl reports no funding or settlement events on this account yet.</div>
              : <div className="ltable"><table><thead><tr>
                <th>When</th><th>Asset</th><th>Event</th><th className="r">Amount</th><th className="r">Balance after</th>
              </tr></thead><tbody>
                {funding.rows.slice(0, 200).map((e, i) => (
                  <tr key={`${e.at}-${i}`}>
                    <td className="dim">{new Date(e.at).toLocaleString()}</td>
                    <td className="amt">{e.marketId != null ? (MARKETS[e.marketId]?.symbol ?? `M${e.marketId}`) : '-'}</td>
                    <td className="dim">type {e.et}</td>
                    <td className={`r ${e.amount >= 0 ? 'long-c' : 'short-c'}`}>{e.amount >= 0 ? '+' : ''}{formatUSD(e.amount)}</td>
                    <td className="r">{e.balanceAfter != null ? formatUSD(e.balanceAfter) : '-'}</td>
                  </tr>
                ))}
              </tbody></table></div>
        )}

        {pill === 'transfers' && (
          perplBlocked ? <div className="empty">{perplBlockedMsg}</div>
            : transfers.length === 0 ? <div className="empty">Perpl reports no deposits or withdrawals on this account yet.</div>
              : <div className="ltable"><table><thead><tr>
                <th>When</th><th>Event</th><th className="r">Amount</th><th className="r">Balance after</th>
              </tr></thead><tbody>
                {transfers.slice(0, 200).map((e, i) => (
                  <tr key={`${e.at}-${i}`}>
                    <td className="dim">{new Date(e.at).toLocaleString()}</td>
                    <td>{e.amount >= 0 ? 'Deposit / credit' : 'Withdrawal / debit'} <span className="dim">· type {e.et}</span></td>
                    <td className={`r ${e.amount >= 0 ? 'long-c' : 'short-c'}`}>{e.amount >= 0 ? '+' : ''}{formatUSD(e.amount)}</td>
                    <td className="r">{e.balanceAfter != null ? formatUSD(e.balanceAfter) : '-'}</td>
                  </tr>
                ))}
              </tbody></table></div>
        )}

        {pill === 'copyh' && (
          copies.length === 0 ? <div className="empty">Every order placed from copy trade will be listed here with the trader it mirrored.</div>
            : <div className="ltable"><table><thead><tr><th>Asset</th><th>Side</th><th className="r">Margin</th><th>From</th><th className="r">When</th></tr></thead><tbody>
              {copies.map((c, i) => (
                <tr key={i}>
                  <td className="amt">{c.symbol}</td>
                  <td><span className={`tag ${c.side === 'long' ? 'long' : 'short'}`}>{c.side === 'long' ? 'Long' : 'Short'} {c.leverage}x</span></td>
                  <td className="r">{formatUSD(c.amount_usd)}</td>
                  <td className="mono dim">{shortenAddress(c.copied_from)}</td>
                  <td className="r dim">{new Date(c.timestamp).toLocaleString()}</td>
                </tr>
              ))}
            </tbody></table></div>
        )}

        {pill === 'copyp' && (
          perf.isLoading ? <div className="empty">Loading copy performance…</div>
            : perfRows.length === 0 ? <div className="empty">No copied trades yet. Once you copy a trader, their per-trader win rate, volume and net result appear here.</div>
              : <div style={{ display: 'grid', gap: 10 }}>{perfRows.map((l) => (
                <div className="card" key={l.leader_wallet} style={{ padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <div>
                      <span className="mono" style={{ fontWeight: 600 }}>{shortenAddress(l.leader_wallet)}</span>
                      <div className="dim" style={{ fontSize: 12 }}>{l.total_copies} copies · {l.closed_copies} closed · {l.open_copies} open</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div className={l.total_pnl >= 0 ? 'long-c' : 'short-c'} style={{ fontSize: 18, fontWeight: 700 }}>{l.total_pnl >= 0 ? '+' : ''}{formatUSD(l.total_pnl)}</div>
                      <div className="dim" style={{ fontSize: 12 }}>{l.win_rate}% win · {formatUSD(l.total_volume)} vol</div>
                    </div>
                  </div>
                </div>
              ))}</div>
        )}
      </div>
    </div>
  );
}

function Calendar({ series, all }: {
  series: DayPoint[];
  all: Map<string, { pnl: number; trades: number; volume: number }>;
}) {
  // Six-week grid, Mon..Sun, coloured by that day's REALIZED result.
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - 41);
  const dow = (start.getDay() + 6) % 7;
  start.setDate(start.getDate() - dow);
  const ups = [...all.values()].filter((v) => v.pnl > 0).map((v) => v.pnl);
  const maxUp = Math.max(1, ...ups);
  const cells: JSX.Element[] = [];
  const heads = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  heads.forEach((h) => cells.push(<div key={'h' + h} className="h">{h}</div>));
  void series;
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const key = d.toISOString().slice(0, 10);
    const hit = all.get(key);
    const future = d > today;
    let cls = '';
    let val = '';
    if (hit && hit.trades > 0) {
      if (hit.pnl < 0) cls = 'neg';
      else if (hit.pnl > 0) {
        const f = hit.pnl / maxUp;
        cls = f > 0.66 ? 'win3' : f > 0.33 ? 'win2' : 'win1';
      }
      val = (hit.pnl > 0 ? '+' : '') + Math.round(hit.pnl);
    }
    cells.push(
      <div key={i} className={`${cls} ${future ? 'fut' : ''}`.trim()}
        title={hit ? `${key}: ${hit.trades} trade(s), ${hit.pnl >= 0 ? '+' : ''}${hit.pnl.toFixed(2)}` : key}>
        <span>{d.getDate()}</span>{val && <b>{val}</b>}
      </div>,
    );
  }
  return <div className="heat">{cells}</div>;
}
