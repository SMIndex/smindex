import { useState, useEffect, useRef, lazy, Suspense } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSignMessage } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api, { getTraderPositions, saveTrade, saveOrder, getTradeHistory, getOrderHistory } from '@/lib/api';
import { useMarketStore } from '@/stores/marketStore';
import {
  closePosition, getSession, getPerplPayload, connectPerpl,
  openTradingWs, setAccountId, tryAutoReconnect, MARKET_CONFIGS,
  cancelOrder, modifyOrder, placeSlTpOrder, getOpenOrders, subscribeOpenOrders,
  type OpenOrder,
} from '@/lib/perplTrading';
import { calculateEstimatedFee, PERPL_CLOSE_FEE_BPS } from '@/lib/perplFees';
import { formatUSD, formatPrice, formatTimeAgo } from '@/lib/formatters';
import { useToast } from '@/components/common/Toast';
import { useCopyStore } from '@/stores/copyStore';
import { MARKETS } from '@/config/constants';

const TradePnlCard = lazy(() => import('@/components/common/TradePnlCard'));

export default function PositionsPanel() {
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
  const [posSl, setPosSl] = useState('');
  const [posTp, setPosTp] = useState('');
  const [savingSlTp, setSavingSlTp] = useState(false);
  // Armed (untriggered) trigger orders live from the trading WS (server-side
  // orders — they are NOT on-chain, so the REST orders list can't see them).
  const [wsOrders, setWsOrders] = useState<OpenOrder[]>(getOpenOrders());
  useEffect(() => subscribeOpenOrders(() => setWsOrders(getOpenOrders())), []);
  const triggerOrders = wsOrders.filter((o) => o.st === 8);
  const [closeMenuOpen, setCloseMenuOpen] = useState<number | null>(null);
  const [tab, setTab] = useState<'positions' | 'orders' | 'history' | 'order_history'>('positions');
  const [pnlCardTrade, setPnlCardTrade] = useState<any>(null);

  const { data, refetch } = useQuery({
    queryKey: ['terminal-positions', address],
    queryFn: () => getTraderPositions(address!),
    enabled: !!address,
    refetchInterval: 5000,
  });

  // SL/TP orders query
  const { data: slTpOrders, refetch: refetchSlTp } = useQuery({
    queryKey: ['sl-tp-orders'],
    queryFn: () => api.get('/api/sl-tp/orders?status=active').then((r) => r.data),
    enabled: !!address,
    refetchInterval: 10000,
  });

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

  // Close the partial close menu when clicking outside
  useEffect(() => {
    if (closeMenuOpen === null) return;
    const handler = (e: MouseEvent) => setCloseMenuOpen(null);
    const timer = setTimeout(() => document.addEventListener('click', handler), 0);
    return () => { clearTimeout(timer); document.removeEventListener('click', handler); };
  }, [closeMenuOpen]);

  // Refetch when order placed or SL/TP triggered
  useEffect(() => {
    const handler = () => { refetch(); refetchSlTp(); refetchHistory(); refetchOrderHistory(); };
    window.addEventListener('order_placed', handler);
    window.addEventListener('sl_tp_triggered', handler);
    return () => {
      window.removeEventListener('order_placed', handler);
      window.removeEventListener('sl_tp_triggered', handler);
    };
  }, [refetch, refetchSlTp]);

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
        const d = await getTraderPositions(address);
        if (d?.account_id) setAccountId(d.account_id);
      } catch {}
      return true;
    } catch (err: any) {
      toast.error(err?.message || 'Failed to connect');
      return false;
    }
  };

  const handleClose = async (pos: any, pct: number = 100) => {
    if (!(await ensureConnected())) return;
    setClosingId(pos.market_id);
    setCloseMenuOpen(null);
    try {
      const mcfg = MARKET_CONFIGS[pos.market_id];
      const closeSize = pct === 100 ? pos.size : pos.size * (pct / 100);
      await closePosition({
        marketId: pos.market_id,
        side: pos.side,
        size: closeSize,
        priceDecimals: mcfg?.priceDecimals ?? 1,
        sizeDecimals: mcfg?.sizeDecimals ?? 5,
        markPrice: pos.mark_price,
      });
      toast.success(`Closed ${pct}% of ${pos.symbol} ${pos.side}`);

      // Full close: cancel any armed SL/TP triggers left on this market so
      // they can't act on a future position.
      if (pct === 100) {
        for (const o of getOpenOrders().filter((t) => t.st === 8 && t.mkt === pos.market_id)) {
          try { await cancelOrder({ marketId: pos.market_id, oid: o.oid }); } catch {}
        }
      }

      // Save close trade + order to history
      try {
        const cSize = pct === 100 ? pos.size : pos.size * (pct / 100);
        const closePrice = pos.mark_price;
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
          }),
        ]);
      } catch (e) {
        console.warn('[trade-history] failed to save close trade:', e);
      }

      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Close failed');
    } finally {
      setClosingId(null);
    }
  };

  const handleCancelSlTp = async (orderId: number) => {
    setCancellingId(orderId);
    try {
      await api.delete(`/api/sl-tp/orders/${orderId}`);
      toast.success('SL/TP cancelled');
      refetchSlTp();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Cancel failed');
    } finally {
      setCancellingId(null);
    }
  };

  const handleCloseAll = async () => {
    if (!(await ensureConnected())) return;
    setClosingAll(true);
    let closed = 0;
    for (const pos of (data?.positions || [])) {
      try {
        const mcfg = MARKET_CONFIGS[pos.market_id];
        await closePosition({
          marketId: pos.market_id,
          side: pos.side,
          size: pos.size,
          priceDecimals: mcfg?.priceDecimals ?? 1,
          sizeDecimals: mcfg?.sizeDecimals ?? 5,
          markPrice: pos.mark_price,
        });
        closed++;
      } catch (err: any) {
        toast.error(`Failed to close ${pos.symbol}: ${err?.message || 'unknown'}`);
      }
    }
    if (closed > 0) {
      toast.success(`Closed ${closed} position${closed > 1 ? 's' : ''}`);
      refetch();
    }
    setClosingAll(false);
  };

  const handleSetSlTp = async (pos: any) => {
    const sl = posSl ? parseFloat(posSl) : 0;
    const tp = posTp ? parseFloat(posTp) : 0;
    if (!sl && !tp) { toast.error('Enter an SL and/or TP price'); return; }
    const mark = pos.mark_price;
    if (sl) {
      if (pos.side === 'long' && sl >= mark) { toast.error('SL must be below current price for longs'); return; }
      if (pos.side === 'short' && sl <= mark) { toast.error('SL must be above current price for shorts'); return; }
    }
    if (tp) {
      if (pos.side === 'long' && tp <= mark) { toast.error('TP must be above current price for longs'); return; }
      if (pos.side === 'short' && tp >= mark) { toast.error('TP must be below current price for shorts'); return; }
    }
    if (!(await ensureConnected())) return;
    setSavingSlTp(true);
    const mcfg = MARKET_CONFIGS[pos.market_id];
    let placed = 0;
    try {
      for (const [kind, trig] of [['sl', sl], ['tp', tp]] as [('sl' | 'tp'), number][]) {
        if (!trig) continue;
        try {
          await placeSlTpOrder({
            marketId: pos.market_id, side: pos.side, orderType: kind, triggerPrice: trig,
            size: pos.size, priceDecimals: mcfg?.priceDecimals ?? 1, sizeDecimals: mcfg?.sizeDecimals ?? 5,
          });
          placed++;
        } catch (e: any) {
          toast.error(`${kind.toUpperCase()} failed: ${e?.message || 'unknown error'}`);
        }
      }
      if (placed > 0) {
        toast.success(`${placed} trigger order${placed > 1 ? 's' : ''} placed on Perpl for ${pos.symbol}`);
        setSlTpFor(null);
        setPosSl('');
        setPosTp('');
      }
    } finally {
      setSavingSlTp(false);
    }
  };

  const handleCancelOrder = async (ord: any) => {
    if (ord?.order_id == null) return;
    if (!(await ensureConnected())) return;
    setCancellingId(ord.order_id);
    try {
      await cancelOrder({ marketId: ord.market_id, oid: ord.order_id });
      toast.success(`Order #${ord.order_id} canceled`);
      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Cancel failed');
    } finally {
      setCancellingId(null);
    }
  };

  const startEditOrder = (ord: any) => {
    setEditingOrderId(ord.order_id);
    setEditPrice(ord.price != null ? String(ord.price) : '');
    setEditSize(ord.size != null ? String(ord.size) : '');
  };

  const handleSaveEdit = async (ord: any) => {
    const newPrice = parseFloat(editPrice);
    const newSize = parseFloat(editSize);
    if (!(newPrice > 0) || !(newSize > 0)) {
      toast.error('Enter a valid price and size');
      return;
    }
    if (!(await ensureConnected())) return;
    setSavingEdit(true);
    try {
      const mcfg = MARKET_CONFIGS[ord.market_id];
      await modifyOrder({
        marketId: ord.market_id,
        oid: ord.order_id,
        newPrice,
        newSize,
        leverage: ord.leverage ?? 1,
        priceDecimals: mcfg?.priceDecimals ?? 1,
        sizeDecimals: mcfg?.sizeDecimals ?? 5,
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

  const positions = data?.positions || [];
  const orders = data?.orders || [];
  const activeSlTp = slTpOrders || [];

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
        {activeSlTp.length > 0 && (
          <button
            onClick={() => setTab('orders')}
            className="text-[10px] px-1.5 py-0.5 bg-warning/10 text-warning rounded font-medium"
          >
            SL/TP: {activeSlTp.length}
          </button>
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

                const posSlTp = activeSlTp.filter((o: any) => o.market_id === pos.market_id && o.side === pos.side);
                const sl = posSlTp.find((o: any) => o.order_type === 'sl');
                const tp = posSlTp.find((o: any) => o.order_type === 'tp');

                return (
                  <div key={pos.market_id} className="border-t border-text-secondary/5 px-3 py-2 text-[11px]">
                    {/* Row 1: Market + PnL + Close buttons */}
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-text-primary">{pos.symbol}</span>
                        <span className={clsx('text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                          {pos.side} {pos.leverage}x
                        </span>
                        <span className={clsx('font-bold', livePnl >= 0 ? 'text-success' : 'text-danger')}>
                          {livePnl >= 0 ? '+' : ''}{formatUSD(livePnl)}
                          <span className="text-[10px] ml-0.5">({roe >= 0 ? '+' : ''}{roe.toFixed(1)}%)</span>
                        </span>
                      </div>
                      {/* Close buttons — always visible, inline */}
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => {
                            if (slTpFor === pos.market_id) { setSlTpFor(null); return; }
                            setSlTpFor(pos.market_id);
                            setPosSl(sl ? String(sl.trigger_price) : '');
                            setPosTp(tp ? String(tp.trigger_price) : '');
                          }}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] bg-warning/15 text-warning"
                        >
                          SL/TP
                        </button>
                        {[25, 50, 100].map((pct) => (
                          <button
                            key={pct}
                            onClick={() => handleClose(pos, pct)}
                            disabled={closingId === pos.market_id}
                            className={clsx(
                              'px-2 py-1 rounded text-[10px] font-semibold min-h-[28px]',
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
                      {sl && <span className="px-1 bg-danger/10 text-danger rounded">SL {formatPrice(sl.trigger_price, dec)}</span>}
                      {tp && <span className="px-1 bg-success/10 text-success rounded">TP {formatPrice(tp.trigger_price, dec)}</span>}
                    </div>
                    {/* Inline SL/TP editor — places native Perpl trigger orders */}
                    {slTpFor === pos.market_id && (
                      <div className="flex items-center gap-2 mt-1.5">
                        <input
                          type="number"
                          value={posSl}
                          onChange={(e) => setPosSl(e.target.value)}
                          className="input-field text-[11px] py-1 w-28"
                          placeholder={`SL (${pos.side === 'long' ? 'below' : 'above'})`}
                        />
                        <input
                          type="number"
                          value={posTp}
                          onChange={(e) => setPosTp(e.target.value)}
                          className="input-field text-[11px] py-1 w-28"
                          placeholder={`TP (${pos.side === 'long' ? 'above' : 'below'})`}
                        />
                        <button
                          onClick={() => handleSetSlTp(pos)}
                          disabled={savingSlTp}
                          className="px-2 py-1 rounded text-[10px] font-semibold bg-accent text-white"
                        >
                          {savingSlTp ? 'Placing...' : 'Place on Perpl'}
                        </button>
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
          {orders.length === 0 && activeSlTp.length === 0 && triggerOrders.length === 0 ? (
            <div className="flex items-center justify-center h-full text-xs text-text-secondary">No open orders</div>
          ) : (
            <>
              {/* Armed SL/TP trigger orders — live on Perpl's servers (st:8 Untriggered) */}
              {triggerOrders.length > 0 && (
                <>
                  <div className="px-3 py-1.5 bg-bg-secondary text-[10px] font-medium text-text-secondary uppercase tracking-wider">
                    Active SL / TP on Perpl
                  </div>
                  {triggerOrders.map((o) => {
                    const mcfg = MARKET_CONFIGS[o.mkt];
                    const cfg = MARKETS[o.mkt];
                    const pd = 10 ** (mcfg?.priceDecimals ?? 1);
                    const sd = 10 ** (mcfg?.sizeDecimals ?? 5);
                    const trigPrice = (o.tp ?? o.p ?? 0) / pd;
                    // t:3 closes a long, t:4 closes a short; condition tells SL vs TP
                    const isSl = o.t === 3 ? (o.tpc === 4 || o.tpc === 2) : (o.tpc === 3 || o.tpc === 1);
                    return (
                      <div key={`trig-${o.oid}`} className="border-t border-text-secondary/5 px-3 py-2 text-[11px] flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-text-primary">{cfg?.symbol ?? `M${o.mkt}`}</span>
                          <span className={clsx('font-semibold uppercase text-[10px]', isSl ? 'text-danger' : 'text-success')}>
                            {isSl ? 'STOP LOSS' : 'TAKE PROFIT'}
                          </span>
                          <span className="text-text-secondary text-[10px]">
                            closes {o.t === 3 ? 'long' : 'short'} · trigger ${formatPrice(trigPrice, cfg?.decimals ?? 2)} · size {(o.s / sd).toFixed(mcfg?.sizeDecimals ?? 4)}
                          </span>
                        </div>
                        <button
                          onClick={() => handleCancelOrder({ market_id: o.mkt, order_id: o.oid })}
                          disabled={cancellingId === o.oid}
                          className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] bg-danger/20 text-danger hover:bg-danger hover:text-white transition-all"
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
                  : (ord.side ?? '—').toUpperCase();
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
                              className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] bg-accent/15 text-accent"
                            >
                              {editingOrderId === ord.order_id ? 'Cancel edit' : 'Edit'}
                            </button>
                            <button
                              onClick={() => handleCancelOrder(ord)}
                              disabled={cancellingId === ord.order_id}
                              className="px-2 py-1 rounded text-[10px] font-semibold min-h-[28px] bg-danger/20 text-danger hover:bg-danger hover:text-white transition-all"
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
                        Price: {ord.price != null ? `$${formatPrice(ord.price, dec)}` : '—'}
                      </span>
                      <span>
                        Size: {ord.size != null ? ord.size : '—'}
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
              {activeSlTp.length > 0 && (
                <>
                  <div className="px-3 py-1.5 bg-bg-secondary text-[10px] font-medium text-text-secondary uppercase tracking-wider">
                    Stop Loss / Take Profit
                  </div>
                  {activeSlTp.map((o: any) => {
                    const cfg = MARKETS[o.market_id];
                    const dec = cfg?.decimals ?? 2;
                    return (
                      <div key={`sltp-${o.id}`} className="flex items-center gap-2 sm:gap-3 px-3 py-2 border-t border-text-secondary/5 text-[11px] flex-wrap">
                        <span className="font-bold text-text-primary">{cfg?.symbol || `M${o.market_id}`}</span>
                        <span className={clsx('font-semibold uppercase text-[10px]', o.side === 'long' ? 'text-success' : 'text-danger')}>{o.side}</span>
                        <span className={clsx('text-[10px] font-semibold px-1.5 py-0.5 rounded', o.order_type === 'sl' ? 'bg-danger/10 text-danger' : 'bg-success/10 text-success')}>
                          {o.order_type === 'sl' ? 'Stop Loss' : 'Take Profit'}
                        </span>
                        <span className="text-text-primary">@ ${formatPrice(o.trigger_price, dec)}</span>
                        <button
                          onClick={() => handleCancelSlTp(o.id)}
                          disabled={cancellingId === o.id}
                          className="ml-auto text-[10px] px-2 py-0.5 rounded bg-text-secondary/10 text-text-secondary hover:text-danger hover:bg-danger/10"
                        >
                          {cancellingId === o.id ? '...' : 'Cancel'}
                        </button>
                      </div>
                    );
                  })}
                </>
              )}
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
