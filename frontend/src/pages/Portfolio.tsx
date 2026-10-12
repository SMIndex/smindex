/**
 * Portfolio: rebuilt 2026-10-08 on the shared account store and on Perpl's
 * own account reads.
 *
 * What it used to be: a donut with nothing in it, a "Trading record" that
 * counted copied trades only, "No account history yet", an empty calendar and
 * "Positions (0)" next to a real open position, because the page read
 * /api/account-health once on mount (its SL/TP coverage came from a table
 * nothing wrote to any more) and the calendar differenced 4-hourly equity
 * snapshots.
 *
 * What it is now:
 *   positions / orders / balance / margin  -> the shared store (uncached chain
 *     read + the venue's own order frames, same numbers as the Terminal);
 *   trades, fees, funding, deposits, equity, calendar, trading record ->
 *     Perpl's own authenticated reads (fills + account history), signed in the
 *     browser with the one-click key, so no wallet popup and no estimates.
 *     Trade rows are labelled with the source (terminal / copy / auto-copy) by
 *     matching the venue order id against our own order records.
 */
import { oneAtATime } from '@/lib/oneAtATime';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { createChart, ColorType, AreaSeries } from 'lightweight-charts';

import { useAuth } from '@/hooks/useAuth';
import { usePerplAccount } from '@/hooks/usePerplAccount';
import { triggerPrice, triggerSize, triggersFor } from '@/lib/accountStore';
import { getOrderHistory } from '@/lib/api';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatUSD, shortenAddress } from '@/lib/formatters';
import {
  buildEvents, buildLedger, dailyPnl, realizedSince, sinceMs, startOfUtcDay, totalsFor,
  type LedgerTrade,
} from '@/lib/perplLedger';
import {
  NoPerplKeyError, getPerplAccountHistory, getPerplFills, hasPerplKey, pageAll,
} from '@/lib/perplRest';
import { closePosition, getSession, openTradingWs, requireMarketConfig, setAccountId, tryAutoReconnect, getPerplPayload, connectPerpl } from '@/lib/perplTrading';
import { useSignMessage } from 'wagmi';
import { useToast } from '@/components/common/Toast';
import SlTpEditor from '@/components/common/SlTpEditor';
import PnLCalendar from '@/components/portfolio/PnLCalendar';
import LoadingSpinner from '@/components/common/LoadingSpinner';

type Tab = 'positions' | 'orders' | 'trades' | 'history' | 'equity';
type SourceFilter = 'all' | 'terminal' | 'copy_trade' | 'auto_copy' | 'other';

const SOURCE_LABEL: Record<string, string> = {
  manual: 'Terminal',
  terminal: 'Terminal',
  copy_trade: 'Copy',
  auto_copy: 'Auto-copy',
  other: 'Other / perpl.xyz',
};

function Stat({ label, value, sub, tone }: {
  label: string; value: string; sub?: string; tone?: 'good' | 'bad' | 'muted';
}) {
  return (
    <div className="card py-3 px-3 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-text-secondary truncate">{label}</div>
      <div className={clsx('text-base sm:text-lg font-bold truncate',
        tone === 'good' ? 'text-success' : tone === 'bad' ? 'text-danger'
          : tone === 'muted' ? 'text-text-secondary' : 'text-text-primary')}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-text-secondary truncate">{sub}</div>}
    </div>
  );
}

function EquityChart({ series }: { series: Array<{ t: number; equity: number }> }) {
  const holder = useRef<HTMLDivElement>(null);
  const chart = useRef<any>(null);
  useEffect(() => {
    if (!holder.current || series.length < 2) return;
    if (chart.current) { chart.current.remove(); chart.current = null; }
    const c = createChart(holder.current, {
      width: holder.current.clientWidth,
      height: 220,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#6b7280', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(107,114,128,0.1)' }, horzLines: { color: 'rgba(107,114,128,0.1)' } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });
    const s = c.addSeries(AreaSeries, {
      lineColor: '#6366f1', topColor: 'rgba(99,102,241,0.30)', bottomColor: 'rgba(99,102,241,0.02)', lineWidth: 2,
    });
    // one point per second max, ascending (lightweight-charts requirement)
    const seen = new Set<number>();
    s.setData(series.filter((p) => !seen.has(p.t) && seen.add(p.t) !== undefined)
      .map((p) => ({ time: p.t as any, value: p.equity })));
    c.timeScale().fitContent();
    chart.current = c;
    const onResize = () => holder.current && c.applyOptions({ width: holder.current.clientWidth });
    window.addEventListener('resize', onResize);
    return () => { window.removeEventListener('resize', onResize); c.remove(); chart.current = null; };
  }, [series]);
  if (series.length < 2) {
    return (
      <div className="text-xs text-text-secondary text-center py-10">
        Perpl has fewer than two account events for this wallet, so there is no equity line to draw yet.
      </div>
    );
  }
  return <div ref={holder} className="w-full" />;
}

export default function Portfolio() {
  const { address } = useAuth();
  const account = usePerplAccount(address);
  const toast = useToast();
  const { signMessageAsync } = useSignMessage();
  const [tab, setTab] = useState<Tab>('positions');
  const [source, setSource] = useState<SourceFilter>('all');
  const [slTpFor, setSlTpFor] = useState<number | null>(null);
  const [closing, setClosing] = useState<number | null>(null);

  const keyPresent = hasPerplKey(address);

  // --- Perpl's own reads (no wallet signature: signed with the one-click key)
  const fillsQ = useQuery({
    queryKey: ['perpl-fills', address],
    queryFn: () => pageAll((page) => getPerplFills(address!, page), 400),
    enabled: !!address && keyPresent,
    staleTime: 60_000,
    retry: false,
  });
  const eventsQ = useQuery({
    queryKey: ['perpl-account-history', address],
    queryFn: () => pageAll((page) => getPerplAccountHistory(address!, page), 400),
    enabled: !!address && keyPresent,
    staleTime: 60_000,
    retry: false,
  });
  // our own order records, only to label each trade with where it came from
  const ourOrdersQ = useQuery({
    queryKey: ['order-history-portfolio'],
    queryFn: () => getOrderHistory({ limit: 200 }),
    enabled: !!address,
    staleTime: 60_000,
  });

  const ledger = useMemo(() => buildLedger((fillsQ.data?.rows ?? []) as any), [fillsQ.data]);
  const events = useMemo(() => buildEvents(eventsQ.data?.rows ?? []), [eventsQ.data]);
  const sourceByOid = useMemo(() => {
    const m = new Map<string, string>();
    (ourOrdersQ.data ?? []).forEach((o: any) => {
      if (o.order_id) m.set(String(o.order_id), o.source || 'manual');
    });
    return m;
  }, [ourOrdersQ.data]);

  const tradeSource = (t: LedgerTrade): string => {
    const hit = sourceByOid.get(String(t.closeOid))
      ?? t.openOids.map((o) => sourceByOid.get(String(o))).find(Boolean);
    return hit ?? 'other';
  };

  const shownTrades = useMemo(() => (
    source === 'all' ? ledger.trades
      : ledger.trades.filter((t) => {
        const s = tradeSource(t);
        if (source === 'terminal') return s === 'manual' || s === 'terminal';
        if (source === 'other') return !['manual', 'terminal', 'copy_trade', 'auto_copy'].includes(s);
        return s === source;
      })
  ), [ledger.trades, source, sourceByOid]);

  const shownTotals = useMemo(() => totalsFor(shownTrades), [shownTrades]);
  const realizedToday = useMemo(() => realizedSince(ledger.trades, startOfUtcDay()), [ledger.trades]);
  const realized7d = useMemo(() => realizedSince(ledger.trades, sinceMs(7)), [ledger.trades]);
  const funding = useMemo(() => {
    const market = events.events.filter((e) => e.kind === 'market');
    return {
      received: market.filter((e) => e.amount > 0).reduce((a, e) => a + e.amount, 0),
      paid: market.filter((e) => e.amount < 0).reduce((a, e) => a + Math.abs(e.amount), 0),
      count: market.length,
    };
  }, [events]);
  const equitySeries = useMemo(() => {
    const pts = events.events
      .filter((e) => e.balanceAfter != null)
      .map((e) => ({ t: Math.floor(e.at / 1000), equity: (e.balanceAfter ?? 0) + (e.lockedAfter ?? 0) }))
      .sort((a, b) => a.t - b.t);
    if (account.accountValue > 0) pts.push({ t: Math.floor(Date.now() / 1000), equity: account.accountValue });
    return pts;
  }, [events, account.accountValue]);
  const daily = useMemo(() => dailyPnl(ledger.trades), [ledger.trades]);

  const perplReadError = (q: typeof fillsQ) => {
    const e: any = q.error;
    if (!e) return null;
    if (e instanceof NoPerplKeyError || e?.name === 'NoPerplKeyError') return e.message;
    return e?.response?.data?.detail || e?.message || 'Perpl read failed';
  };

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

  const handleClose = (pos: typeof account.positions[number]) => oneAtATime(`close:${pos.marketId}`, () => handleCloseOnce(pos));
  const handleCloseOnce = async (pos: typeof account.positions[number]) => {
    if (!(await ensureConnected())) return;
    let mcfg;
    try {
      mcfg = requireMarketConfig(pos.marketId);
    } catch {
      toast.error('Market settings not loaded. Refresh and try again.');
      return;
    }
    setClosing(pos.marketId);
    try {
      await closePosition({
        marketId: pos.marketId, side: pos.side, size: pos.size,
        priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
        markPrice: pos.markPrice,
      });
      toast.success(`Closed ${pos.symbol} ${pos.side}`);
      window.dispatchEvent(new CustomEvent('position_closed'));
    } catch (e: any) {
      toast.error(e?.message || 'Close failed');
    } finally {
      setClosing(null);
    }
  };

  if (!address) {
    return <div className="card text-center py-10 text-sm text-text-secondary">Connect your wallet to see your portfolio</div>;
  }
  if (!account.at && account.loading) {
    return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>;
  }

  const fillsErr = perplReadError(fillsQ);
  const dec = (mid: number) => MARKETS[mid]?.decimals ?? 2;

  return (
    <div className="space-y-4">
      {/* ---------------- header ---------------- */}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg sm:text-xl font-bold text-text-primary">Portfolio</h1>
        <div className="text-[10px] text-text-secondary">
          account #{account.accountId || '-'} · {shortenAddress(address)} ·{' '}
          {account.at ? `updated ${Math.max(0, Math.round((Date.now() - account.at) / 1000))}s ago` : 'reading…'}
          {account.error && <span className="text-warning"> · {account.error}</span>}
        </div>
      </div>

      {account.venueAccountMismatch && (
        <div className="card border border-warning/40 text-[11px] text-warning">
          Perpl's account frame and the chain read disagree: {account.venueAccountMismatch}. The numbers below are the
          chain read.
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 sm:gap-3">
        <Stat label="Account value" value={formatUSD(account.accountValue)}
              sub="available + margin + unrealized" />
        <Stat label="Available" value={formatUSD(account.available)} sub="free to trade" />
        <Stat label="Margin in use" value={formatUSD(account.marginInUse)}
              sub={`${account.positions.length} position${account.positions.length === 1 ? '' : 's'}`} />
        <Stat label="Unrealized P/L" value={`${account.unrealizedPnl >= 0 ? '+' : ''}${formatUSD(account.unrealizedPnl)}`}
              tone={account.unrealizedPnl >= 0 ? 'good' : 'bad'} sub="incl. funding" />
        <Stat label="Realized today" value={keyPresent && !fillsErr ? `${realizedToday >= 0 ? '+' : ''}${formatUSD(realizedToday)}` : '-'}
              tone={realizedToday >= 0 ? 'good' : 'bad'}
              sub={keyPresent && !fillsErr ? `7d ${realized7d >= 0 ? '+' : ''}${formatUSD(realized7d)} · all ${ledger.totals.netPnl >= 0 ? '+' : ''}${formatUSD(ledger.totals.netPnl)}` : 'needs one-click key'} />
        <Stat label="Fees paid" value={keyPresent && !fillsErr ? formatUSD(ledger.feesUsdAllFills) : '-'}
              sub={keyPresent && !fillsErr
                ? `funding ${funding.received > funding.paid ? '+' : '-'}${formatUSD(Math.abs(funding.received - funding.paid))}`
                : 'needs one-click key'} />
      </div>

      {!keyPresent && (
        <div className="card border border-accent/30 text-[11px] text-text-secondary">
          Trades, fees, funding, deposits and the equity curve come from Perpl's own account endpoints, which are signed
          with the one-click key in this browser. Enable one-click trading in Settings and they appear here, no further
          wallet signature is ever needed for reading.
        </div>
      )}
      {keyPresent && fillsErr && (
        <div className="card border border-warning/40 text-[11px] text-warning">Perpl history read failed: {fillsErr}</div>
      )}

      {/* ---------------- tabs ---------------- */}
      <div className="flex gap-1 bg-bg-secondary rounded-lg p-1 overflow-x-auto scrollbar-hide">
        {([
          ['positions', `Positions (${account.positions.length})`],
          ['orders', `Orders (${account.chainOrders.length + account.triggers.length})`],
          ['trades', `Trades (${ledger.trades.length})`],
          ['history', `Account history (${events.events.length})`],
          ['equity', 'Equity & calendar'],
        ] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={clsx('px-3 py-2 rounded-md text-xs font-medium whitespace-nowrap shrink-0 min-h-[36px]',
              tab === k ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}>
            {label}
          </button>
        ))}
      </div>

      {/* ---------------- positions ---------------- */}
      {tab === 'positions' && (
        <div className="card p-0 overflow-hidden">
          {account.positions.length === 0 ? (
            <div className="text-center py-10 text-sm text-text-secondary">
              No open positions on Perpl for this wallet.
            </div>
          ) : (
            <div className="divide-y divide-text-secondary/5">
              {account.positions.map((p) => {
                const { sl, tp } = triggersFor(account, p.marketId, p.side);
                const d = dec(p.marketId);
                return (
                  <div key={p.marketId} className="px-3 sm:px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-bold text-text-primary">{p.symbol}</span>
                        <span className={clsx('text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded',
                          p.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                          {p.side} {p.leverage}x
                        </span>
                        <span className={clsx('font-bold', p.pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {p.pnl >= 0 ? '+' : ''}{formatUSD(p.pnl)}
                          <span className="text-[10px] ml-0.5">({p.roePct >= 0 ? '+' : ''}{p.roePct.toFixed(1)}%)</span>
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        <button onClick={() => setSlTpFor(slTpFor === p.marketId ? null : p.marketId)}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[32px] max-[820px]:min-h-[44px] bg-warning/15 text-warning">
                          SL/TP
                        </button>
                        <button onClick={() => handleClose(p)} disabled={closing === p.marketId}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[32px] max-[820px]:min-h-[44px] bg-danger text-white disabled:opacity-60">
                          {closing === p.marketId ? '…' : 'Close 100%'}
                        </button>
                      </div>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-text-secondary">
                      <span>Size: {p.size}</span>
                      <span>Entry: ${formatPrice(p.entryPrice, d)}</span>
                      <span>Mark: ${formatPrice(p.markPrice, d)}</span>
                      <span>Margin: {formatUSD(p.deposit)}</span>
                      <span>Funding: {p.fundingPnl >= 0 ? '+' : ''}{formatUSD(p.fundingPnl)}</span>
                      <span className="text-danger">
                        Liq: {p.liqPrice != null ? `$${formatPrice(p.liqPrice, d)}` : '-'}
                        {p.liqDistancePct != null && ` (${p.liqDistancePct.toFixed(1)}% away)`}
                      </span>
                      {sl ? (
                        <span className="px-1 bg-danger/10 text-danger rounded">
                          SL {triggerPrice(sl) != null ? formatPrice(triggerPrice(sl)!, d) : '-'}
                          {triggerSize(sl) != null && triggerSize(sl)! + 1e-12 < p.size ? ` (${triggerSize(sl)} of ${p.size})` : ''}
                        </span>
                      ) : <span className="px-1 bg-warning/10 text-warning rounded">no SL</span>}
                      {tp ? (
                        <span className="px-1 bg-success/10 text-success rounded">
                          TP {triggerPrice(tp) != null ? formatPrice(triggerPrice(tp)!, d) : '-'}
                        </span>
                      ) : <span className="px-1 bg-warning/10 text-warning rounded">no TP</span>}
                    </div>
                    {slTpFor === p.marketId && (
                      <div className="mt-2">
                        <SlTpEditor account={account} ensureConnected={ensureConnected}
                          position={{
                            marketId: p.marketId, symbol: p.symbol, side: p.side,
                            size: p.size, markPrice: p.markPrice, leverage: p.leverage,
                          }} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ---------------- orders ---------------- */}
      {tab === 'orders' && (
        <div className="space-y-3">
          <div className="card p-0 overflow-hidden">
            <div className="px-3 py-2 bg-bg-secondary text-[10px] uppercase tracking-wider text-text-secondary">
              Trigger orders on Perpl (SL / TP)
            </div>
            {!account.venueSession ? (
              <div className="px-3 py-6 text-center text-[11px] text-text-secondary">
                Trigger orders are held by Perpl, not on-chain, connect to Perpl (one-click, no popup) to list them.
              </div>
            ) : account.triggers.length === 0 ? (
              <div className="px-3 py-6 text-center text-[11px] text-text-secondary">No armed SL/TP on Perpl.</div>
            ) : (
              <div className="divide-y divide-text-secondary/5">
                {account.triggers.map((o) => {
                  const d = dec(o.mkt);
                  const px = triggerPrice(o);
                  const sz = triggerSize(o);
                  const isSl = o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1);
                  return (
                    <div key={o.oid} className="px-3 py-2 flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="font-bold text-text-primary">{MARKETS[o.mkt]?.symbol ?? `M${o.mkt}`}</span>
                      <span className={clsx('text-[10px] font-semibold uppercase', isSl ? 'text-danger' : 'text-success')}>
                        {isSl ? 'stop loss' : 'take profit'}
                      </span>
                      <span className="text-text-secondary">closes {o.t === 3 ? 'long' : 'short'}</span>
                      <span className="text-text-primary">trigger {px != null ? `$${formatPrice(px, d)}` : '-'}</span>
                      <span className="text-text-secondary">size {sz != null ? sz : 'not reported by Perpl'}</span>
                      <span className="text-text-secondary">#{o.oid}</span>
                      {o.st === 9 && <span className="text-warning">triggered</span>}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="card p-0 overflow-hidden">
            <div className="px-3 py-2 bg-bg-secondary text-[10px] uppercase tracking-wider text-text-secondary">
              Resting orders on-chain
            </div>
            {account.chainOrders.length === 0 ? (
              <div className="px-3 py-6 text-center text-[11px] text-text-secondary">No resting orders.</div>
            ) : (
              <div className="divide-y divide-text-secondary/5">
                {account.chainOrders.map((o: any) => (
                  <div key={`${o.lock_id}-${o.order_id}`} className="px-3 py-2 flex flex-wrap items-center gap-2 text-[11px]">
                    <span className="font-bold text-text-primary">{o.symbol}</span>
                    <span className="text-text-secondary uppercase text-[10px]">{String(o.order_type).replace(/_/g, ' ')}</span>
                    <span className="text-text-primary">{o.price != null ? `$${formatPrice(o.price, dec(o.market_id))}` : '-'}</span>
                    <span className="text-text-secondary">size {o.size ?? '-'}</span>
                    <span className="text-text-secondary">margin {formatUSD(o.margin_locked ?? 0)}</span>
                    <span className="text-text-secondary">#{o.order_id}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---------------- trades ---------------- */}
      {tab === 'trades' && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3">
            <Stat label="Trades" value={String(shownTotals.trades)}
                  sub={`${shownTotals.wins}W / ${shownTotals.losses}L`} />
            <Stat label="Win rate" value={shownTotals.trades ? `${shownTotals.winRatePct.toFixed(1)}%` : '-'} />
            <Stat label="Volume" value={formatUSD(shownTotals.volumeUsd)} sub="closed notional" />
            <Stat label="Net result" value={`${shownTotals.netPnl >= 0 ? '+' : ''}${formatUSD(shownTotals.netPnl)}`}
                  tone={shownTotals.netPnl >= 0 ? 'good' : 'bad'} sub="after fees" />
          </div>
          <div className="flex flex-wrap gap-1">
            {(['all', 'terminal', 'copy_trade', 'auto_copy', 'other'] as SourceFilter[]).map((f) => (
              <button key={f} onClick={() => setSource(f)}
                className={clsx('px-2 py-1 rounded text-[10px] font-medium min-h-[32px]',
                  source === f ? 'bg-accent text-white' : 'bg-bg-secondary text-text-secondary')}>
                {f === 'all' ? 'All sources' : SOURCE_LABEL[f] ?? f}
              </button>
            ))}
          </div>
          <div className="card p-0 overflow-hidden">
            {!keyPresent || fillsErr ? (
              <div className="px-3 py-8 text-center text-[11px] text-text-secondary">
                {fillsErr ?? 'Enable one-click trading to read your Perpl fills.'}
              </div>
            ) : fillsQ.isLoading ? (
              <div className="flex justify-center py-10"><LoadingSpinner /></div>
            ) : shownTrades.length === 0 ? (
              <div className="px-3 py-8 text-center text-[11px] text-text-secondary">
                {ledger.fillCount === 0
                  ? 'Perpl has no fills for this account yet.'
                  : 'No closed trades for this source.'}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-secondary border-b border-text-secondary/10">
                      <th className="px-3 py-2 text-left font-medium">Closed</th>
                      <th className="px-2 py-2 text-left font-medium">Market</th>
                      <th className="px-2 py-2 text-left font-medium">Side</th>
                      <th className="px-2 py-2 text-right font-medium">Size</th>
                      <th className="px-2 py-2 text-right font-medium">Entry</th>
                      <th className="px-2 py-2 text-right font-medium">Exit</th>
                      <th className="px-2 py-2 text-right font-medium">Fee</th>
                      <th className="px-2 py-2 text-right font-medium">Net P/L</th>
                      <th className="px-3 py-2 text-left font-medium">Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownTrades.slice(0, 200).map((t) => {
                      const d = dec(t.marketId);
                      const src = tradeSource(t);
                      return (
                        <tr key={`${t.closeOid}-${t.closedAt}`} className="border-t border-text-secondary/5">
                          <td className="px-3 py-1.5 text-text-secondary whitespace-nowrap">
                            {new Date(t.closedAt).toLocaleString()}
                          </td>
                          <td className="px-2 py-1.5 text-text-primary font-medium">
                            {MARKETS[t.marketId]?.symbol ?? `M${t.marketId}`}
                          </td>
                          <td className={clsx('px-2 py-1.5 uppercase', t.side === 'long' ? 'text-success' : 'text-danger')}>
                            {t.side}
                          </td>
                          <td className="px-2 py-1.5 text-right">{t.size}</td>
                          <td className="px-2 py-1.5 text-right">${formatPrice(t.entryPrice, d)}</td>
                          <td className="px-2 py-1.5 text-right">${formatPrice(t.exitPrice, d)}</td>
                          <td className="px-2 py-1.5 text-right text-text-secondary">{formatUSD(t.fees)}</td>
                          <td className={clsx('px-2 py-1.5 text-right font-semibold', t.netPnl >= 0 ? 'text-success' : 'text-danger')}>
                            {t.netPnl >= 0 ? '+' : ''}{formatUSD(t.netPnl)}
                          </td>
                          <td className="px-3 py-1.5 text-text-secondary">{SOURCE_LABEL[src] ?? src}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          {(ledger.skipped.unknownMarket > 0 || ledger.skipped.unmatchedClose > 0) && (
            <div className="text-[10px] text-text-secondary">
              {ledger.skipped.unknownMarket > 0 && `${ledger.skipped.unknownMarket} fill(s) on a market whose settings are not loaded were left out. `}
              {ledger.skipped.unmatchedClose > 0 && `${ledger.skipped.unmatchedClose} close fill(s) had no matching open in the last ${ledger.fillCount} fills (the position was opened before that).`}
            </div>
          )}
        </div>
      )}

      {/* ---------------- account history ---------------- */}
      {tab === 'history' && (
        <div className="card p-0 overflow-hidden">
          {!keyPresent || perplReadError(eventsQ) ? (
            <div className="px-3 py-8 text-center text-[11px] text-text-secondary">
              {perplReadError(eventsQ) ?? 'Enable one-click trading to read your Perpl account history.'}
            </div>
          ) : eventsQ.isLoading ? (
            <div className="flex justify-center py-10"><LoadingSpinner /></div>
          ) : events.events.length === 0 ? (
            <div className="px-3 py-8 text-center text-[11px] text-text-secondary">
              Perpl reports no account events (deposits, withdrawals, funding, settlements) for this account.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-text-secondary border-b border-text-secondary/10">
                    <th className="px-3 py-2 text-left font-medium">Time</th>
                    <th className="px-2 py-2 text-left font-medium">Event</th>
                    <th className="px-2 py-2 text-left font-medium">Market</th>
                    <th className="px-2 py-2 text-right font-medium">Amount</th>
                    <th className="px-2 py-2 text-right font-medium">Fee</th>
                    <th className="px-2 py-2 text-right font-medium">Balance after</th>
                    <th className="px-3 py-2 text-right font-medium">Locked after</th>
                  </tr>
                </thead>
                <tbody>
                  {events.events.slice(0, 300).map((e, i) => (
                    <tr key={`${e.at}-${i}`} className="border-t border-text-secondary/5">
                      <td className="px-3 py-1.5 text-text-secondary whitespace-nowrap">{new Date(e.at).toLocaleString()}</td>
                      <td className="px-2 py-1.5 text-text-primary">
                        {e.kind === 'transfer' ? (e.amount >= 0 ? 'Deposit / credit' : 'Withdrawal / debit')
                          : e.kind === 'market' ? 'Market event (funding / settlement)' : 'Account event'}
                        <span className="text-text-secondary"> · type {e.et}</span>
                      </td>
                      <td className="px-2 py-1.5 text-text-secondary">
                        {e.marketId != null ? (MARKETS[e.marketId]?.symbol ?? `M${e.marketId}`) : '-'}
                      </td>
                      <td className={clsx('px-2 py-1.5 text-right', e.amount >= 0 ? 'text-success' : 'text-danger')}>
                        {e.amount >= 0 ? '+' : ''}{formatUSD(e.amount)}
                      </td>
                      <td className="px-2 py-1.5 text-right text-text-secondary">{e.fee ? formatUSD(e.fee) : '-'}</td>
                      <td className="px-2 py-1.5 text-right">{e.balanceAfter != null ? formatUSD(e.balanceAfter) : '-'}</td>
                      <td className="px-3 py-1.5 text-right text-text-secondary">{e.lockedAfter != null ? formatUSD(e.lockedAfter) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ---------------- equity + calendar ---------------- */}
      {tab === 'equity' && (
        <div className="space-y-3">
          <div className="card">
            <div className="flex items-baseline justify-between mb-2">
              <h2 className="text-sm font-semibold text-text-primary">Account value</h2>
              <span className="text-[10px] text-text-secondary">
                from Perpl's account events{equitySeries.length ? ` · ${equitySeries.length} points` : ''}
              </span>
            </div>
            {!keyPresent ? (
              <div className="text-xs text-text-secondary text-center py-10">
                Enable one-click trading and the curve is built from Perpl's own account history.
              </div>
            ) : <EquityChart series={equitySeries} />}
          </div>
          <div className="card">
            <div className="flex items-baseline justify-between mb-2">
              <h2 className="text-sm font-semibold text-text-primary">Daily realized P/L</h2>
              <span className="text-[10px] text-text-secondary">
                {daily.size ? `${daily.size} trading day(s) from real fills` : 'from real fills'}
              </span>
            </div>
            <PnLCalendar daily={daily} />
          </div>
        </div>
      )}
    </div>
  );
}
