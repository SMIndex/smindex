import { useState, useEffect, useMemo, useRef, lazy, Suspense } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSignMessage } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import { oneAtATime } from '@/lib/oneAtATime';
import api, { getTraderPositions, saveTrade, saveOrder, getTradeHistory, getOrderHistory } from '@/lib/api';
import { useMarketStore } from '@/stores/marketStore';
import {
  closePosition, getSession, getPerplPayload, connectPerpl,
  openTradingWs, setAccountId, tryAutoReconnect, MARKET_CONFIGS,
  cancelOrder, modifyOrder, placeSlTpOrder, getOpenOrders, subscribeOpenOrders,
  getVenuePositions, getPositionsSeenAt,
  requireMarketConfig,
  type OpenOrder,
} from '@/lib/perplTrading';
import { calculateEstimatedFee, PERPL_CLOSE_FEE_BPS } from '@/lib/perplFees';
import { formatUSD, formatPrice, formatTimeAgo } from '@/lib/formatters';
import { useToast } from '@/components/common/Toast';
import { useCopyStore } from '@/stores/copyStore';
import { MARKETS } from '@/config/constants';
import { reportLeftoverCancelled } from '@/lib/alertsBridge';
import { mobileConfirm } from '@/lib/mobileConfirm';
import { usePerplAccount } from '@/hooks/usePerplAccount';
import { triggerPrice, triggerSize, triggersFor } from '@/lib/accountStore';
import SlTpEditor from '@/components/common/SlTpEditor';

const TradePnlCard = lazy(() => import('@/components/common/TradePnlCard'));

export default function PositionsPanel({ initialTab }: { initialTab?: 'positions' | 'orders' | 'history' | 'order_history' } = {}) {
  const { address } = useAuth();
  const { signMessageAsync } = useSignMessage();
  const markets = useMarketStore((s) => s.markets);
  const toast = useToast();
  const getCopySource = useCopyStore((s) => s.getCopySource);
  const queryClient = useQueryClient();
  const [closingId, setClosingId] = useState<number | null>(null);
  const [closingAll, setClosingAll] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [editingOrderId, setEditingOrderId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState('');
  const [editSize, setEditSize] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  // Per-position SL/TP editor (native Perpl trigger orders)
  const [slTpFor, setSlTpFor] = useState<number | null>(null);
  // Armed (untriggered) trigger orders live from the trading WS (server-side
  // orders: they are NOT on-chain, so the REST orders list can't see them).
  const [wsOrders, setWsOrders] = useState<OpenOrder[]>(getOpenOrders());
  useEffect(() => subscribeOpenOrders(() => setWsOrders(getOpenOrders())), []);
  // Armed TP/SL = the venue's own trigger rows. A leg we just submitted is in
  // the same store the moment the venue acks its oid (perplTrading remembers
  // what we sent under that oid), so there is no separate "pending" list any
  // more and no 15s window where the row could show the wrong thing.
  const triggerOrders = wsOrders.filter((o) => o.st === 8 || o.st === 9);
  const [closeMenuOpen, setCloseMenuOpen] = useState<number | null>(null);
  const [tab, setTab] = useState<'positions' | 'orders' | 'history' | 'order_history'>(initialTab ?? 'positions');
  const [pnlCardTrade, setPnlCardTrade] = useState<any>(null);

  // ONE shared account store (2026-10-08), the same positions, balance and
  // margin the Portfolio, Dashboard and Copy dashboard show, from one uncached
  // chain read. This panel used to run its own 5s poll, which is how the
  // Terminal could say "Positions (1) · $35.02" while Portfolio said
  // "0 positions · $43.78" at the same moment.
  const account = usePerplAccount(address);
  const refetch = account.refresh;
  const data = useMemo(() => ({
    account_id: account.accountId,
    balance: account.available,
    margin_used: account.marginInUse,
    // snake_case shape kept so the rest of this panel is untouched
    positions: account.positions.map((p) => ({
      market_id: p.marketId, symbol: p.symbol, side: p.side, size: p.size,
      entry_price: p.entryPrice, mark_price: p.markPrice, pnl: p.pnl,
      delta_pnl: p.deltaPnl, funding_pnl: p.fundingPnl, deposit: p.deposit,
      leverage: p.leverage, notional: p.notional,
    })),
    orders: account.chainOrders,
  }), [account]);

  // Trade history query
  const { data: tradeHistory, refetch: refetchHistory } = useQuery({
    queryKey: ['trade-history-panel'],
    queryFn: () => getTradeHistory({ limit: 50 }),
    enabled: !!address && tab === 'history',
    refetchInterval: 10000,
  });

  // Order history query
  const { data: orderHistoryData, refetch: refetchOrderHistory } = useQuery({
    queryKey: ['order-history-panel'],
    queryFn: () => getOrderHistory({ limit: 50 }),
    enabled: !!address && tab === 'order_history',
    refetchInterval: 10000,
  });

  // Push, not just poll (optimization pass 2026-09-24): the venue's open-order
  // frames (placed / filled / cancelled) arrive on the trading socket the
  // moment an action lands; refetch the on-chain views then instead of waiting
  // for the next 5-10 s poll. Twice, because the chain read can trail the
  // venue frame by a block. Read-only: the polls stay as the fallback.
  useEffect(() => {
    if (!address) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const unsub = subscribeOpenOrders(() => {
      console.log('[order-timing] refetch-scheduled', Date.now());
      for (const ms of [500, 2500]) timers.push(setTimeout(() => {
        refetch('venue-order-frame');
        if (tab === 'history') refetchHistory();
        if (tab === 'order_history') refetchOrderHistory();
      }, ms));
    });
    return () => { unsub(); timers.forEach(clearTimeout); };
  }, [address, tab, refetch, refetchHistory, refetchOrderHistory]);

  // Venue-ack position (display only): a confirmed fill renders immediately
  // from the venue's own ack values; the row is replaced the moment the
  // chain read returns that market, and dropped unconditionally after 30s
  // so a failed chain read can never leave a stale row.
  const [pendingFill, setPendingFill] = useState<any>(null);
  useEffect(() => {
    const h = (e: any) => setPendingFill({ ...e.detail, at: Date.now() });
    window.addEventListener('order_filled', h);
    return () => window.removeEventListener('order_filled', h);
  }, []);
  useEffect(() => {
    console.log('[order-timing] positions-data', Date.now(),
      `n=${data?.positions?.length ?? 'none'}`);
    if (pendingFill && data?.positions?.some((p: any) => p.market_id === pendingFill.marketId)) {
      setPendingFill(null);
    }
  }, [data]);

  // OCO: a TP/SL leg filled (venue frame, dispatched by ingestOrderRows).
  // The chain read trails the fill by a couple of blocks, so poll it briefly;
  // the moment the market reads flat, cancel the sibling leg(s). If it still
  // reads open after the last try (partial-size leg, or a slow chain), the
  // orphan sweep below is the fallback.
  useEffect(() => {
    const h = async (e: any) => {
      const mkt = e.detail?.mkt;
      if (mkt == null || !address) return;
      console.log('[order-timing] oco-check-start', Date.now(), `mkt:${mkt}`);
      for (const wait of [600, 1500, 3000, 6000]) {
        await new Promise((r) => setTimeout(r, wait));
        try {
          const fresh = await getTraderPositions(address, { fresh: true });
          const open = !!fresh?.positions?.some((p: any) => p.market_id === mkt);
          console.log('[order-timing] oco-position-read', Date.now(), `mkt:${mkt} open:${open}`);
          if (!open) {
            const n = await cancelMarketTriggers(mkt, 'OCO: leg filled, position flat');
            if (n > 0) {
              toast.success(`TP/SL filled on ${MARKETS[mkt]?.symbol ?? `M${mkt}`}: cancelled the remaining leg`);
            }
            refetch();
            return;
          }
        } catch {}
      }
      console.log('[order-timing] oco-check-end', Date.now(), `mkt:${mkt} position still open, siblings kept`);
      refetch();
    };
    window.addEventListener('trigger_leg_filled', h);
    return () => window.removeEventListener('trigger_leg_filled', h);
  }, [address]);

  // Orphan sweep (position closed on perpl.xyz or with no tab open): an armed
  // trigger on a market the chain reads flat for >10s straight is a leftover
  // from a closed position: cancel it and say so. The 10s debounce (two 5s
  // position polls) plus the pendingFill guard keeps a just-opened position's
  // fresh TP/SL out of the sweep while the chain read catches up.
  const orphanSince = useRef<Map<number, number>>(new Map());
  const sweepInFlight = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (!data?.positions || !getSession().authenticated) return;
    const now = Date.now();
    const armed = wsOrders.filter((o) => o.st === 8);
    const openMkts = new Set((data.positions as any[]).map((p: any) => p.market_id));
    const pendingMkt = pendingFill && now - pendingFill.at < 30_000 ? pendingFill.marketId : null;
    for (const oid of Array.from(orphanSince.current.keys())) {
      if (!armed.some((o) => o.oid === oid)) orphanSince.current.delete(oid);
    }
    // 2026-10-12: until today every cancel was rejected by the venue, so this
    // sweep never actually removed anything. Now that cancels work, the chain
    // read alone is not enough to remove someone's stop loss: Perpl's own
    // position list (mt:26/27 on the live socket) must ALSO show no position
    // for it. No venue snapshot yet = no sweep.
    const venuePositions = getVenuePositions();
    const venueKnown = getPositionsSeenAt() > 0;
    const venueMkts = new Set(venuePositions.map((p) => p.mkt));
    const venuePids = new Set(venuePositions.map((p) => p.pid));
    const ripe: OpenOrder[] = [];
    for (const o of armed) {
      const lp = (o as any).lp;
      if (openMkts.has(o.mkt) || o.mkt === pendingMkt || !venueKnown
        || venueMkts.has(o.mkt) || (lp != null && venuePids.has(Number(lp)))) {
        orphanSince.current.delete(o.oid); continue;
      }
      const since = orphanSince.current.get(o.oid);
      if (since == null) orphanSince.current.set(o.oid, now);
      else if (now - since > 10_000 && !sweepInFlight.current.has(o.oid)) ripe.push(o);
    }
    if (ripe.length === 0) return;
    (async () => {
      const byMkt = new Map<number, number>();
      for (const o of ripe) {
        sweepInFlight.current.add(o.oid);
        try {
          console.log('[order-timing] orphan-sweep-cancel', Date.now(), `oid:${o.oid} mkt:${o.mkt}`);
          await cancelOrder({ marketId: o.mkt, oid: o.oid });
          orphanSince.current.delete(o.oid);
          byMkt.set(o.mkt, (byMkt.get(o.mkt) ?? 0) + 1);
        } catch (e: any) {
          console.warn('[orphan-sweep] cancel failed', o.oid, e?.message);
        } finally {
          sweepInFlight.current.delete(o.oid);
        }
      }
      for (const [mkt, n] of byMkt) {
        toast.success(`Cancelled ${n} leftover TP/SL order${n > 1 ? 's' : ''} on ${MARKETS[mkt]?.symbol ?? `M${mkt}`}: position was closed`);
        reportLeftoverCancelled(MARKETS[mkt]?.symbol ?? `M${mkt}`, mkt, n, 'position was closed outside the app');
      }
    })();
  }, [data, wsOrders, pendingFill]);

  // Close the partial close menu when clicking outside
  useEffect(() => {
    if (closeMenuOpen === null) return;
    const handler = (e: MouseEvent) => setCloseMenuOpen(null);
    const timer = setTimeout(() => document.addEventListener('click', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('click', handler); };
  }, [closeMenuOpen]);

  // Refetch our own tables when an order is placed. (The account itself is
  // refreshed by the shared store, which listens to the same event.)
  useEffect(() => {
    const handler = () => { refetchHistory(); refetchOrderHistory(); };
    window.addEventListener('order_placed', handler);
    return () => window.removeEventListener('order_placed', handler);
  }, [refetchHistory, refetchOrderHistory]);

  const ensureConnected = async (): Promise<boolean> => {
    const sess = getSession();
    if (sess.authenticated && sess.hasWs) return true;
    if (!address) return false;
    try {
      // Try auto-reconnect first (no wallet popup)
      const reconnected = await tryAutoReconnect(address);
      if (reconnected) return true;

      // Need fresh SIWE sign
      const payload = await getPerplPayload(address);
      const signature = await signMessageAsync({ message: payload.message });
      const nonce = await connectPerpl(address, payload, signature);
      await openTradingWs(nonce, address);
      try {
        const d = await getTraderPositions(address, { fresh: true });
        if (d?.account_id) setAccountId(d.account_id);
      } catch {}
      return true;
    } catch (err: any) {
      toast.error(err?.message || 'Failed to connect');
      return false;
    }
  };

  // Cancel every armed (8) / activated (9) TP-SL trigger on a market. Loud:
  // each failure is logged and surfaced, a silently-orphaned trigger can act
  // on a future position (A2, live repro 2026-09-28).
  // Money-path fix (beta 2026-10-08): nothing is sent for a market whose
  // settings did not load. The old hard-coded fallbacks (priceDecimals ?? 1,
  // sizeDecimals ?? 5) are only right for BTC - on MON they sent s=100000000
  // for 1,000 MON and p=0 (no price bound at all).
  const cfgOrRefuse = (marketId: number) => {
    try {
      return requireMarketConfig(marketId);
    } catch {
      toast.error('Market settings not loaded. Refresh and try again.');
      return null;
    }
  };

  const cancelMarketTriggers = async (marketId: number, why: string): Promise<number> => {
    const armed = getOpenOrders().filter((o) => (o.st === 8 || o.st === 9) && o.mkt === marketId);
    if (armed.length === 0) return 0;
    console.log('[order-timing] auto-cancel-start', Date.now(), `mkt:${marketId} n:${armed.length} (${why})`);
    let ok = 0;
    for (const o of armed) {
      try {
        await cancelOrder({ marketId, oid: o.oid });
        ok++;
      } catch (e: any) {
        console.warn('[auto-cancel] failed', o.oid, e?.message);
      }
    }
    console.log('[order-timing] auto-cancel-done', Date.now(), `mkt:${marketId} ok:${ok}/${armed.length}`);
    reportLeftoverCancelled(MARKETS[marketId]?.symbol ?? `M${marketId}`, marketId, ok, why);
    if (ok < armed.length) {
      toast.error(`Failed to cancel ${armed.length - ok} TP/SL order(s), cancel manually in Orders`);
    }
    return ok;
  };

  // Partial close: shrink surviving triggers to the remaining position size.
  // t:7 Change on a trigger order is unverified against the venue, so this
  // places the replacement first (proven placement path, same trigger price),
  // then cancels the oversized one, a cancel-first order could leave the
  // position unprotected if the re-place failed. The brief overlap is safe:
  // both legs are close-side, the venue rejects an over-close.
  const resizeMarketTriggers = async (pos: any, remaining: number) => {
    const mcfg = cfgOrRefuse(pos.market_id);
    if (!mcfg) return;
    const pd = 10 ** mcfg.priceDecimals;
    const sd = 10 ** mcfg.sizeDecimals;
    const remScaled = Math.round(remaining * sd);
    const oversized = getOpenOrders().filter((o) => o.st === 8 && o.mkt === pos.market_id && o.s > remScaled);
    for (const o of oversized) {
      const tpScaled = o.tp ?? 0;
      if (tpScaled <= 0) {
        console.warn('[auto-resize] trigger without tp, left untouched', o.oid);
        continue;
      }
      const isSl = o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1);
      try {
        await placeSlTpOrder({
          marketId: pos.market_id, side: pos.side, orderType: isSl ? 'sl' : 'tp',
          triggerPrice: tpScaled / pd, size: remaining,
          priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
        });
        await cancelOrder({ marketId: pos.market_id, oid: o.oid });
        console.log('[order-timing] trigger-resized', Date.now(), `oid:${o.oid} -> ${remaining}`);
      } catch (e: any) {
        toast.error(`Could not resize ${isSl ? 'SL' : 'TP'} #${o.oid} to the remaining size: ${e?.message || 'unknown'}`);
      }
    }
  };

  const handleClose = (pos: any, pct: number = 100) =>
    oneAtATime(`close:${pos.market_id}`, () => handleCloseOnce(pos, pct));
  const handleCloseOnce = async (pos: any, pct: number = 100) => {
    const mcfg = cfgOrRefuse(pos.market_id);
    if (!mcfg) return;
    if (!(await ensureConnected())) return;
    const cSz = pct === 100 ? pos.size : pos.size * (pct / 100);
    const closeTx = await mobileConfirm({
      title: `Close ${pct}% of ${pos.symbol} ${pos.side}`, danger: true, confirmLabel: 'Close',
      rows: [['Side', pos.side === 'long' ? 'Sell (close long)' : 'Buy (close short)'],
        ['Size', `${cSz} ${pos.symbol}`], ['Price', `~${formatPrice(pos.mark_price, MARKETS[pos.market_id]?.decimals ?? 2)} (market)`],
        ['Leverage', `${pos.leverage}x`],
        ['Fee', formatUSD(calculateEstimatedFee(cSz * pos.mark_price, PERPL_CLOSE_FEE_BPS))]],
    });
    if (!closeTx.ok) return;
    setClosingId(pos.market_id);
    setCloseMenuOpen(null);
    try {
      const closeSize = pct === 100 ? pos.size : pos.size * (pct / 100);
      const closeRes = await closePosition({
        marketId: pos.market_id,
        side: pos.side,
        size: closeSize,
        priceDecimals: mcfg.priceDecimals,
        sizeDecimals: mcfg.sizeDecimals,
        markPrice: pos.mark_price,
      });
      console.log('[order-timing] close-ack', Date.now(),
        `mkt:${pos.market_id} oid:${closeRes?.orderId} fs:${closeRes?.filledSize} fp:${closeRes?.filledPrice}`);
      toast.success(`Closed ${pct}% of ${pos.symbol} ${pos.side}`);
      closeTx.done(`Closed ${pct}% of ${pos.symbol} ${pos.side}`);

      // Close fill confirmed: same flow, before anything else:
      // full close cancels every surviving TP/SL trigger on the market,
      // partial close shrinks them to the remaining size.
      if (pct === 100) {
        const n = await cancelMarketTriggers(pos.market_id, 'full close');
        if (n > 0) toast.success(`Cancelled ${n} TP/SL order${n > 1 ? 's' : ''} on ${pos.symbol}`);
      } else {
        await resizeMarketTriggers(pos, pos.size - closeSize);
      }

      // Save close trade + order to history, venue ack values when the fill
      // frame carried them (fs/fp), mark-at-click only as fallback (the old
      // estimate-always recording put a close at 82718.9 in a minute whose
      // venue high was 82705.8, not a real print).
      try {
        const cSize = closeRes?.filledSize != null
          ? closeRes.filledSize / (10 ** mcfg.sizeDecimals)
          : (pct === 100 ? pos.size : pos.size * (pct / 100));
        const closePrice = closeRes?.filledPrice != null
          ? closeRes.filledPrice / (10 ** mcfg.priceDecimals)
          : pos.mark_price;
        const closePnl = pos.side === 'long'
          ? (closePrice - pos.entry_price) * cSize
          : (pos.entry_price - closePrice) * cSize;
        const notional = cSize * closePrice;
        const isCopy = !!getCopySource(pos.market_id);
        const src = isCopy ? 'copy_trade' : 'manual';
        const direction = `Close ${pos.side === 'long' ? 'Long' : 'Short'}`;
        // Perpl charges no fee on close (Taker/Maker Close = 0 per docs). Record 0, not a fake estimate.
        const fee = calculateEstimatedFee(notional, PERPL_CLOSE_FEE_BPS);
        await Promise.all([
          saveTrade({
            market_id: pos.market_id, symbol: pos.symbol, side: pos.side,
            action: 'close', order_type: 'market', size: cSize,
            price: closePrice, leverage: pos.leverage,
            notional: Math.round(notional * 100) / 100,
            pnl: Math.round(closePnl * 100) / 100, source: src,
          }),
          saveOrder({
            market_id: pos.market_id, symbol: pos.symbol, direction,
            order_type: 'market', size: cSize, filled_size: cSize,
            order_value: Math.round(notional * 100) / 100,
            price: closePrice, fill_price: closePrice,
            reduce_only: true, status: 'filled',
            fee: Math.round(fee * 100) / 100,
            pnl: Math.round(closePnl * 100) / 100, source: src,
            order_id: closeRes?.orderId,
            raw_response: closeRes || null,
          }),
        ]);
      } catch (e) {
        console.warn('[trade-history] failed to save close trade:', e);
      }

      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Close failed');
      closeTx.fail(err?.message || 'Close failed');
    } finally {
      setClosingId(null);
    }
  };

  const handleCloseAll = () => oneAtATime('close-all', handleCloseAllOnce);
  const handleCloseAllOnce = async () => {
    if (!(await ensureConnected())) return;
    const all = data?.positions || [];
    const allTx = await mobileConfirm({
      title: `Close ALL ${all.length} position${all.length === 1 ? '' : 's'}`, danger: true, confirmLabel: 'Close all',
      rows: all.map((p: any) => [`${p.symbol} ${p.side} ${p.leverage}x`, `${p.size} @ ~${formatPrice(p.mark_price, MARKETS[p.market_id]?.decimals ?? 2)}`] as [string, string])
        .concat([['Fee', '$0.00 (Perpl close fee is 0)']]),
    });
    if (!allTx.ok) return;
    setClosingAll(true);
    let closed = 0;
    for (const pos of (data?.positions || [])) {
      try {
        const mcfg = cfgOrRefuse(pos.market_id);
        if (!mcfg) continue;
        await closePosition({
          marketId: pos.market_id,
          side: pos.side,
          size: pos.size,
          priceDecimals: mcfg.priceDecimals,
          sizeDecimals: mcfg.sizeDecimals,
          markPrice: pos.mark_price,
        });
        closed++;
        // Same rule as a single full close: no surviving triggers (A2).
        const n = await cancelMarketTriggers(pos.market_id, 'close-all');
        if (n > 0) toast.success(`Cancelled ${n} TP/SL order${n > 1 ? 's' : ''} on ${pos.symbol}`);
      } catch (err: any) {
        toast.error(`Failed to close ${pos.symbol}: ${err?.message || 'unknown'}`);
      }
    }
    if (closed > 0) {
      toast.success(`Closed ${closed} position${closed > 1 ? 's' : ''}`);
      refetch();
    }
    if (closed === all.length) allTx.done(`Closed ${closed} position${closed === 1 ? '' : 's'}`);
    else allTx.fail(`Closed ${closed} of ${all.length}; see the error messages`);
    setClosingAll(false);
  };

  // handleSetSlTp REMOVED 2026-10-08 -> components/common/SlTpEditor.tsx.
  // The old version armed a new pair WITHOUT cancelling the armed one, which
  // is how one ETH position ended up with two stop losses and two take
  // profits on 2026-10-08. The shared editor replaces instead of adding, and
  // the Copy dashboard uses the very same component.

  const handleCancelOrder = (ord: any) => oneAtATime(`cancel:${ord?.order_id}`, () => handleCancelOrderOnce(ord));
  const handleCancelOrderOnce = async (ord: any) => {
    if (ord?.order_id == null) return;
    if (!(await ensureConnected())) return;
    const cxTx = await mobileConfirm({
      title: `Cancel order #${ord.order_id}`, danger: true, confirmLabel: 'Cancel order',
      rows: [['Market', MARKETS[ord.market_id]?.symbol ?? `M${ord.market_id}`], ['Order', `#${ord.order_id}`]],
    });
    if (!cxTx.ok) return;
    setCancellingId(ord.order_id);
    try {
      await cancelOrder({ marketId: ord.market_id, oid: ord.order_id });
      toast.success(`Order #${ord.order_id} canceled`);
      cxTx.done(`Order #${ord.order_id} canceled`);
      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Cancel failed');
      cxTx.fail(err?.message || 'Cancel failed');
    } finally {
      setCancellingId(null);
    }
  };

  const startEditOrder = (ord: any) => {
    setEditingOrderId(ord.order_id);
    setEditPrice(ord.price != null ? String(ord.price) : '');
    setEditSize(ord.size != null ? String(ord.size) : '');
  };

  const handleSaveEdit = (ord: any) => oneAtATime(`edit:${ord?.order_id}`, () => handleSaveEditOnce(ord));
  const handleSaveEditOnce = async (ord: any) => {
    const newPrice = parseFloat(editPrice);
    const newSize = parseFloat(editSize);
    if (!(newPrice > 0) || !(newSize > 0)) {
      toast.error('Enter a valid price and size');
      return;
    }
    if (!(await ensureConnected())) return;
    const mcfg = cfgOrRefuse(ord.market_id);
    if (!mcfg) return;
    setSavingEdit(true);
    try {
      await modifyOrder({
        marketId: ord.market_id,
        oid: ord.order_id,
        newPrice,
        newSize,
        leverage: ord.leverage ?? 1,
        priceDecimals: mcfg.priceDecimals,
        sizeDecimals: mcfg.sizeDecimals,
      });
      toast.success(`Order #${ord.order_id} updated`);
      setEditingOrderId(null);
      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Update failed');
    } finally {
      setSavingEdit(false);
    }
  };

  const chainPositions = data?.positions || [];
  // Prepend the venue-ack row only while the chain read does not show that
  // market yet (an add to an existing position keeps the existing row).
  const showPending = pendingFill
    && Date.now() - pendingFill.at < 30_000
    && !chainPositions.some((p: any) => p.market_id === pendingFill.marketId);
  const positions = showPending
    ? [{
        market_id: pendingFill.marketId, symbol: pendingFill.symbol,
        side: pendingFill.side, size: pendingFill.size,
        entry_price: pendingFill.price, mark_price: pendingFill.price,
        pnl: 0, deposit: pendingFill.leverage > 0
          ? (pendingFill.size * pendingFill.price) / pendingFill.leverage : 0,
        leverage: pendingFill.leverage,
        notional: pendingFill.size * pendingFill.price,
        _pending: true,
      }, ...chainPositions]
    : chainPositions;
  const orders = data?.orders || [];

  return (
    <div className="flex flex-col h-full">
      {/* Tabs */}
      <div className="flex items-center gap-3 px-3 py-2 border-b border-text-secondary/10">
        <button
          onClick={() => setTab('positions')}
          className={clsx('text-xs font-medium', tab === 'positions' ? 'text-text-primary' : 'text-text-secondary')}
        >
          Positions ({positions.length})
        </button>
        <button
          onClick={() => setTab('orders')}
          className={clsx('text-xs font-medium', tab === 'orders' ? 'text-text-primary' : 'text-text-secondary')}
        >
          Orders ({orders.length + triggerOrders.length})
        </button>
        <button
          onClick={() => setTab('history')}
          className={clsx('text-xs font-medium', tab === 'history' ? 'text-text-primary' : 'text-text-secondary')}
        >
          Trade History
        </button>
        <button
          onClick={() => setTab('order_history')}
          className={clsx('text-xs font-medium', tab === 'order_history' ? 'text-text-primary' : 'text-text-secondary')}
        >
          Order History
        </button>
        {triggerOrders.length > 0 && (
          <button
            onClick={() => setTab('orders')}
            className="text-[10px] px-1.5 py-0.5 bg-warning/10 text-warning rounded font-medium"
          >
            SL/TP on Perpl: {triggerOrders.length}
          </button>
        )}
        {address && account.noPerplAccount && (
          <span className="text-[10px] px-1.5 py-0.5 text-text-secondary">
            No Perpl account yet. <a href="https://app.perpl.xyz" target="_blank" rel="noopener noreferrer"
              className="text-text-primary underline">Deposit on Perpl</a> to start trading
          </span>
        )}
        {address && !account.venueSession && !account.noPerplAccount && (
          <span className="text-[10px] px-1.5 py-0.5 bg-danger/10 text-danger rounded font-medium"
                title="Orders and SL/TP are read from the live Perpl connection. They are still on Perpl.">
            Reconnecting to Perpl, orders and SL/TP not loaded yet
          </span>
        )}
        {positions.length > 1 && (
          <button
            onClick={handleCloseAll}
            disabled={closingAll}
            className="text-[10px] px-2 py-0.5 rounded bg-danger/10 text-danger hover:bg-danger hover:text-white font-semibold transition-all"
          >
            {closingAll ? 'Closing...' : 'Close All'}
          </button>
        )}
        {data && (
          <span className="ml-auto text-[10px] text-text-secondary hidden sm:inline">
            Balance: {formatUSD(data.balance)} | Margin: {formatUSD(data.margin_used)}
          </span>
        )}
      </div>

      {!address ? (
        <div className="flex-1 flex items-center justify-center text-xs text-text-secondary">Connect wallet</div>
      ) : tab === 'positions' ? (
        <div className="flex-1 overflow-y-auto">
          {positions.length === 0 ? (
            <div className="flex items-center justify-center h-full text-xs text-text-secondary">No open positions</div>
          ) : (
            <div>
              {positions.map((pos: any) => {
                const mcfg = MARKET_CONFIGS[pos.market_id];
                const cfg = MARKETS[pos.market_id];
                const dec = cfg?.decimals ?? 2;
                const m = markets[pos.market_id];
                const livePnl = m ? (pos.side === 'long'
                  ? (m.mark_price - pos.entry_price) * pos.size
                  : (pos.entry_price - m.mark_price) * pos.size) : pos.pnl;
                const roe = pos.deposit > 0 ? (livePnl / pos.deposit) * 100 : 0;

                const mmrFrac = 100 / (mcfg?.maintenanceMarginHdths ?? 2000);
                const mmr = pos.notional * mmrFrac;
                const liqPrice = pos.side === 'long'
                  ? pos.entry_price - (pos.deposit - mmr) / pos.size
                  : pos.entry_price + (pos.deposit - mmr) / pos.size;

                // Armed protection = Perpl's own trigger orders (st:8/9) for
                // this position, not rows in our database.
                const { sl, tp } = triggersFor(account, pos.market_id, pos.side);
                const slPx = sl ? triggerPrice(sl) : null;
                const tpPx = tp ? triggerPrice(tp) : null;

                return (
                  <div key={pos.market_id} className="border-t border-text-secondary/5 px-3 py-2 text-[11px]">
                    {/* Row 1: Market + PnL + Close buttons */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-text-primary">{pos.symbol}</span>
                        <span className={clsx('text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                          {pos.side} {pos.leverage}x
                        </span>
                        {pos._pending && (
                          <span className="text-[9px] px-1 rounded bg-warning/10 text-warning">on-chain sync…</span>
                        )}
                        <span className={clsx('font-bold', livePnl >= 0 ? 'text-success' : 'text-danger')}>
                          {livePnl >= 0 ? '+' : ''}{formatUSD(livePnl)}
                          <span className="text-[10px] ml-0.5">({roe >= 0 ? '+' : ''}{roe.toFixed(1)}%)</span>
                        </span>
                      </div>
                      {/* Close buttons: always visible, inline */}
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => setSlTpFor(slTpFor === pos.market_id ? null : pos.market_id)}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] max-[820px]:min-h-[44px] bg-warning/15 text-warning"
                        >
                          SL/TP
                        </button>
                        {[25, 50, 100].map((pct) => (
                          <button
                            key={pct}
                            onClick={() => handleClose(pos, pct)}
                            disabled={closingId === pos.market_id}
                            className={clsx(
                              'px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] max-[820px]:min-h-[44px]',
                              closingId === pos.market_id ? 'bg-bg-card text-text-secondary' :
                              pct === 100 ? 'bg-danger text-white' : 'bg-danger/20 text-danger',
                            )}
                          >
                            {closingId === pos.market_id ? '...' : `${pct}%`}
                          </button>
                        ))}
                      </div>
                    </div>
                    {/* Row 2: Details */}
                    <div className="flex items-center gap-2 sm:gap-3 mt-1 text-[10px] text-text-secondary flex-wrap">
                      <span>Size: {pos.size.toFixed(mcfg?.sizeDecimals ?? 4)}</span>
                      <span>Entry: ${formatPrice(pos.entry_price, dec)}</span>
                      <span>Mark: ${formatPrice(m?.mark_price ?? pos.mark_price, dec)}</span>
                      <span className="text-danger">Liq: ${formatPrice(Math.max(0, liqPrice), dec)}</span>
                      {sl && (
                        <span className="px-1 bg-danger/10 text-danger rounded">
                          SL {slPx != null ? formatPrice(slPx, dec) : '-'}
                          {triggerSize(sl) != null && triggerSize(sl)! + 1e-12 < pos.size
                            ? ` (${triggerSize(sl)} of ${pos.size})` : ''}
                        </span>
                      )}
                      {tp && (
                        <span className="px-1 bg-success/10 text-success rounded">
                          TP {tpPx != null ? formatPrice(tpPx, dec) : '-'}
                          {triggerSize(tp) != null && triggerSize(tp)! + 1e-12 < pos.size
                            ? ` (${triggerSize(tp)} of ${pos.size})` : ''}
                        </span>
                      )}
                      {!sl && !tp && account.venueSession && (
                        <span className="px-1 bg-warning/10 text-warning rounded">no SL/TP on Perpl</span>
                      )}
                    </div>
                    {/* The shared SL/TP editor, native Perpl trigger orders,
                        replace-not-duplicate, same component as Copy */}
                    {slTpFor === pos.market_id && (
                      <div className="mt-1.5">
                        <SlTpEditor
                          account={account}
                          position={{
                            marketId: pos.market_id, symbol: pos.symbol, side: pos.side,
                            size: pos.size, markPrice: m?.mark_price ?? pos.mark_price,
                            leverage: pos.leverage,
                          }}
                          ensureConnected={ensureConnected}
                          onChanged={() => refetch('sltp-editor')}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : tab === 'orders' ? (
        <div className="flex-1 overflow-y-auto">
          {orders.length === 0 && triggerOrders.length === 0 ? (
            <div className="flex items-center justify-center h-full text-xs text-text-secondary">No open orders</div>
          ) : (
            <>
              {/* Armed SL/TP trigger orders, live on Perpl's servers (st:8 Untriggered) */}
              {triggerOrders.length > 0 && (
                <>
                  <div className="px-3 py-1.5 bg-bg-secondary text-[10px] font-medium text-text-secondary uppercase tracking-wider">
                    Active SL / TP on Perpl
                  </div>
                  {triggerOrders.map((o) => {
                    const mcfg = MARKET_CONFIGS[o.mkt];
                    const cfg = MARKETS[o.mkt];
                    // No guessed decimals here either: without the market's
                    // settings the row shows the market and side, not a number
                    // scaled by a wrong power of ten.
                    const pd = mcfg ? 10 ** mcfg.priceDecimals : null;
                    const sd = mcfg ? 10 ** mcfg.sizeDecimals : null;
                    const trigPrice = pd ? (o.tp ?? o.p ?? 0) / pd : null;
                    // t:3 closes a long, t:4 closes a short; condition tells SL vs TP
                    const isSl = o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1);
                    return (
                      <div key={`trig-${o.oid}`} className="border-t border-text-secondary/5 px-3 py-2 text-[11px] flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-text-primary">{cfg?.symbol ?? `M${o.mkt}`}</span>
                          <span className={clsx('font-semibold uppercase text-[10px]', isSl ? 'text-danger' : 'text-success')}>
                            {isSl ? 'STOP LOSS' : 'TAKE PROFIT'}
                          </span>
                          {(o as any)._local && (
                            <span className="text-[9px] px-1 rounded bg-warning/10 text-warning">syncing…</span>
                          )}
                          <span className="text-text-secondary text-[10px]">
                            closes {o.t === 3 ? 'long' : 'short'} · trigger {trigPrice != null ? `$${formatPrice(trigPrice, cfg?.decimals ?? 2)}` : '-'} · size {sd && mcfg && Number.isFinite(o.s / sd) ? (o.s / sd).toFixed(mcfg.sizeDecimals) : '-'}
                          </span>
                        </div>
                        <button
                          onClick={() => handleCancelOrder({ market_id: o.mkt, order_id: o.oid })}
                          disabled={cancellingId === o.oid}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] max-[820px]:min-h-[44px] bg-danger/20 text-danger hover:bg-danger hover:text-white transition-all"
                        >
                          {cancellingId === o.oid ? '...' : 'Cancel'}
                        </button>
                      </div>
                    );
                  })}
                </>
              )}
              {orders.map((ord: any, i: number) => {
                const cfg = MARKETS[ord.market_id];
                const dec = cfg?.decimals ?? 2;
                const otype: string = ord.order_type ?? 'unknown';
                const sideLabel = ord.side === 'buy' ? 'BUY'
                  : ord.side === 'sell' ? 'SELL'
                  : ord.side === 'trigger' ? (otype === 'stop_loss' ? 'STOP LOSS' : 'TAKE PROFIT')
                  : (ord.side ?? '-').toUpperCase();
                const sideColor = ord.side === 'buy' ? 'text-success'
                  : ord.side === 'sell' ? 'text-danger'
                  : 'text-warning';
                const typeLabel = otype.replace(/_/g, ' ');
                return (
                  <div
                    key={`ord-${ord.lock_id ?? i}`}
                    className="border-t border-text-secondary/5 px-3 py-2 text-[11px]"
                  >
                    {/* Row 1: symbol + side + type + margin */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-text-primary">{ord.symbol ?? `M${ord.market_id}`}</span>
                        <span className={clsx('font-semibold uppercase text-[10px]', sideColor)}>
                          {sideLabel}
                        </span>
                        <span className="text-text-secondary capitalize text-[10px]">
                          {typeLabel}
                        </span>
                        {ord.leverage != null && (
                          <span className="text-text-secondary text-[10px]">
                            {ord.leverage}x
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1">
                        <span className="text-warning font-medium mr-1">
                          {formatUSD(ord.margin_locked ?? 0)}
                        </span>
                        {ord.order_id != null && (
                          <>
                            <button
                              onClick={() => (editingOrderId === ord.order_id ? setEditingOrderId(null) : startEditOrder(ord))}
                              className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] max-[820px]:min-h-[44px] bg-accent/15 text-accent"
                            >
                              {editingOrderId === ord.order_id ? 'Cancel edit' : 'Edit'}
                            </button>
                            <button
                              onClick={() => handleCancelOrder(ord)}
                              disabled={cancellingId === ord.order_id}
                              className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] max-[820px]:min-h-[44px] bg-danger/20 text-danger hover:bg-danger hover:text-white transition-all"
                            >
                              {cancellingId === ord.order_id ? '...' : 'Cancel'}
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                    {/* Inline edit: modify price/size in place (t:7 Change) */}
                    {editingOrderId === ord.order_id && (
                      <div className="flex items-center gap-2 mt-1.5">
                        <input
                          type="number"
                          value={editPrice}
                          onChange={(e) => setEditPrice(e.target.value)}
                          className="input-field text-[11px] py-1 w-28"
                          placeholder="New price"
                        />
                        <input
                          type="number"
                          value={editSize}
                          onChange={(e) => setEditSize(e.target.value)}
                          className="input-field text-[11px] py-1 w-24"
                          placeholder="New size"
                        />
                        <button
                          onClick={() => handleSaveEdit(ord)}
                          disabled={savingEdit}
                          className="px-2 py-1 rounded text-[10px] font-semibold bg-accent text-white"
                        >
                          {savingEdit ? 'Saving...' : 'Save'}
                        </button>
                      </div>
                    )}
                    {/* Row 2: price, size, notional */}
                    <div className="flex items-center gap-2 sm:gap-3 mt-1 text-[10px] text-text-secondary flex-wrap">
                      <span>
                        Price: {ord.price != null ? `$${formatPrice(ord.price, dec)}` : '-'}
                      </span>
                      <span>
                        Size: {ord.size != null ? ord.size : '-'}
                      </span>
                      {ord.notional != null && (
                        <span>Notional: {formatUSD(ord.notional)}</span>
                      )}
                      {ord.order_id != null && (
                        <span className="text-text-secondary/60">#{ord.order_id}</span>
                      )}
                      {ord.expiry_block != null && (
                        <span className="text-warning/70">Expires block #{ord.expiry_block}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </>
          )}
        </div>
      ) : tab === 'history' ? (
        <div className="flex-1 overflow-y-auto">
          {!tradeHistory || tradeHistory.length === 0 ? (
            <div className="flex items-center justify-center h-full text-xs text-text-secondary">No trade history</div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-secondary border-b border-text-secondary/10">
                      <th className="px-3 py-1.5 text-left font-medium">Time</th>
                      <th className="px-2 py-1.5 text-left font-medium">Coin</th>
                      <th className="px-2 py-1.5 text-left font-medium">Direction</th>
                      <th className="px-2 py-1.5 text-right font-medium">Price</th>
                      <th className="px-2 py-1.5 text-right font-medium">Size</th>
                      <th className="px-2 py-1.5 text-right font-medium">Trade Value</th>
                      <th className="px-2 py-1.5 text-right font-medium">Fee</th>
                      <th className="px-2 py-1.5 text-right font-medium">PnL</th>
                      <th className="px-2 py-1.5 text-left font-medium">Source</th>
                      <th className="px-1 py-1.5"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {tradeHistory.map((t: any) => {
                      const cfg = MARKETS[t.market_id];
                      const dec = cfg?.decimals ?? 2;
                      const direction = `${t.action === 'close' ? 'Close' : 'Open'} ${t.side === 'long' ? 'Long' : 'Short'}`;
                      const dirColor = direction.includes('Long') ? 'text-success' : 'text-danger';
                      return (
                        <tr key={t.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                          <td className="px-3 py-1.5 text-text-secondary whitespace-nowrap">
                            {t.time ? new Date(t.time).toLocaleString() : '--'}
                          </td>
                          <td className="px-2 py-1.5 font-bold text-text-primary">{t.symbol}</td>
                          <td className={clsx('px-2 py-1.5 font-semibold', dirColor)}>{direction}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{formatPrice(t.price, dec)}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{t.size != null ? t.size.toLocaleString(undefined, { maximumFractionDigits: cfg?.sizeDecimals ?? 4 }) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{t.notional != null ? formatUSD(t.notional) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-secondary">{t.fee != null ? formatUSD(t.fee) : '$0.00'}</td>
                          <td className="px-2 py-1.5 text-right">
                            {t.pnl != null ? (
                              <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                                {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                              </span>
                            ) : '--'}
                          </td>
                          <td className="px-2 py-1.5">
                            <span className={clsx('text-[9px] px-1.5 py-0.5 rounded font-medium',
                              t.source === 'copy_trade' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary'
                            )}>
                              {t.source === 'copy_trade' ? 'Social' : 'Trade'}
                            </span>
                          </td>
                          <td className="px-1 py-1.5">
                            {t.pnl != null && (
                              <button onClick={() => setPnlCardTrade({ ...t, direction })} className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent hover:bg-accent/20">
                                Share
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {/* Mobile cards */}
              <div className="sm:hidden">
                {tradeHistory.map((t: any) => {
                  const cfg = MARKETS[t.market_id];
                  const dec = cfg?.decimals ?? 2;
                  const direction = `${t.action === 'close' ? 'Close' : 'Open'} ${t.side === 'long' ? 'Long' : 'Short'}`;
                  const dirColor = direction.includes('Long') ? 'text-success' : 'text-danger';
                  return (
                    <div key={t.id} className="border-t border-text-secondary/5 px-3 py-2 text-[11px]">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-text-primary">{t.symbol}</span>
                          <span className={clsx('font-semibold', dirColor)}>{direction}</span>
                          <span className={clsx('text-[9px] px-1 py-0.5 rounded font-medium',
                            t.source === 'copy_trade' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary'
                          )}>
                            {t.source === 'copy_trade' ? 'Social' : 'Trade'}
                          </span>
                        </div>
                        <div className="flex items-center gap-2">
                          {t.pnl != null ? (
                            <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                              {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                            </span>
                          ) : (
                            <span className="text-text-primary font-bold">{t.notional != null ? formatUSD(t.notional) : '--'}</span>
                          )}
                          {t.pnl != null && (
                            <button onClick={() => setPnlCardTrade({ ...t, direction })} className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">
                              Share
                            </button>
                          )}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                        <span>{t.time ? new Date(t.time).toLocaleString() : '--'}</span>
                        <span>Price: {formatPrice(t.price, dec)}</span>
                        <span>Size: {t.size?.toLocaleString(undefined, { maximumFractionDigits: cfg?.sizeDecimals ?? 4 })}</span>
                        <span>Fee: {t.fee != null ? formatUSD(t.fee) : '$0.00'}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      ) : tab === 'order_history' ? (
        <div className="flex-1 overflow-y-auto">
          {!orderHistoryData || orderHistoryData.length === 0 ? (
            <div className="flex items-center justify-center h-full text-xs text-text-secondary">No order history</div>
          ) : (
            <>
              <div className="hidden sm:block overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-secondary border-b border-text-secondary/10">
                      <th className="px-3 py-1.5 text-left font-medium">Time</th>
                      <th className="px-2 py-1.5 text-left font-medium">Type</th>
                      <th className="px-2 py-1.5 text-left font-medium">Coin</th>
                      <th className="px-2 py-1.5 text-left font-medium">Direction</th>
                      <th className="px-2 py-1.5 text-right font-medium">Size</th>
                      <th className="px-2 py-1.5 text-right font-medium">Filled</th>
                      <th className="px-2 py-1.5 text-right font-medium">Order Value</th>
                      <th className="px-2 py-1.5 text-right font-medium">Price</th>
                      <th className="px-2 py-1.5 text-right font-medium">Fill Price</th>
                      <th className="px-2 py-1.5 text-right font-medium">Fee</th>
                      <th className="px-2 py-1.5 text-right font-medium">PnL</th>
                      <th className="px-2 py-1.5 text-left font-medium">Status</th>
                      <th className="px-2 py-1.5 text-right font-medium">Order ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orderHistoryData.map((o: any) => {
                      const cfg = MARKETS[o.market_id];
                      const dec = cfg?.decimals ?? 2;
                      const dirColor = o.direction?.includes('Long') ? 'text-success' : 'text-danger';
                      const stColor = o.status === 'filled' ? 'text-success' : o.status === 'failed' ? 'text-danger' : o.status === 'open' ? 'text-accent' : 'text-warning';
                      return (
                        <tr key={o.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                          <td className="px-3 py-1.5 text-text-secondary whitespace-nowrap">{o.time ? new Date(o.time).toLocaleString() : '--'}</td>
                          <td className="px-2 py-1.5 text-text-secondary capitalize">{o.order_type}</td>
                          <td className="px-2 py-1.5 font-bold text-text-primary">{o.symbol}</td>
                          <td className={clsx('px-2 py-1.5 font-semibold', dirColor)}>{o.direction}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{o.size?.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{o.filled_size != null && o.filled_size > 0 ? o.filled_size.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{o.order_value != null ? formatUSD(o.order_value) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{o.price != null ? formatPrice(o.price, dec) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-primary">{o.fill_price != null ? formatPrice(o.fill_price, dec) : '--'}</td>
                          <td className="px-2 py-1.5 text-right text-text-secondary">{o.fee != null ? formatUSD(o.fee) : '$0.00'}</td>
                          <td className="px-2 py-1.5 text-right">
                            {o.pnl != null ? (
                              <span className={clsx('font-bold', o.pnl >= 0 ? 'text-success' : 'text-danger')}>
                                {o.pnl >= 0 ? '+' : ''}{formatUSD(o.pnl)}
                              </span>
                            ) : '--'}
                          </td>
                          <td className={clsx('px-2 py-1.5 font-semibold capitalize', stColor)}>{o.status}</td>
                          <td className="px-2 py-1.5 text-right text-text-secondary font-mono text-[9px]">{o.order_id || '--'}</td>
                          <td className="px-1 py-1.5">
                            {o.pnl != null && (
                              <button onClick={() => setPnlCardTrade(o)} className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent hover:bg-accent/20">
                                Share
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="sm:hidden">
                {orderHistoryData.map((o: any) => {
                  const cfg = MARKETS[o.market_id];
                  const dec = cfg?.decimals ?? 2;
                  const dirColor = o.direction?.includes('Long') ? 'text-success' : 'text-danger';
                  const stColor = o.status === 'filled' ? 'text-success' : o.status === 'failed' ? 'text-danger' : o.status === 'open' ? 'text-accent' : 'text-warning';
                  return (
                    <div key={o.id} className="border-t border-text-secondary/5 px-3 py-2 text-[11px]">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-text-primary">{o.symbol}</span>
                          <span className={clsx('font-semibold', dirColor)}>{o.direction}</span>
                          <span className="text-text-secondary capitalize">{o.order_type}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {o.pnl != null && (
                            <span className={clsx('font-bold', o.pnl >= 0 ? 'text-success' : 'text-danger')}>
                              {o.pnl >= 0 ? '+' : ''}{formatUSD(o.pnl)}
                            </span>
                          )}
                          {o.pnl != null && (
                            <button onClick={() => setPnlCardTrade(o)} className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">Share</button>
                          )}
                          <span className={clsx('font-semibold capitalize', stColor)}>{o.status}</span>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                        <span>{o.time ? new Date(o.time).toLocaleString() : '--'}</span>
                        <span>Size: {o.size?.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                        {o.order_value != null && <span>Value: {formatUSD(o.order_value)}</span>}
                        {o.fill_price != null && <span>Fill: {formatPrice(o.fill_price, dec)}</span>}
                        {o.fee != null && o.fee > 0 && <span>Fee: {formatUSD(o.fee)}</span>}
                        {o.order_id && <span className="font-mono">#{o.order_id}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      ) : null}

      {pnlCardTrade && (
        <Suspense fallback={null}>
          <TradePnlCard trade={pnlCardTrade} onClose={() => setPnlCardTrade(null)} />
        </Suspense>
      )}
    </div>
  );
}
