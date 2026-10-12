import { useState, lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api, { getTradeHistory, getOrderHistory } from '@/lib/api';
import { formatUSD, formatPrice, formatTimeAgo } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const PnlCard = lazy(() => import('@/components/common/PnlCard'));
const TradePnlCard = lazy(() => import('@/components/common/TradePnlCard'));

export default function OrderHistoryPage() {
  const { address, hydrated } = useAuth();
  const [marketFilter, setMarketFilter] = useState<number | null>(null);
  const [tab, setTab] = useState<'trades' | 'order_history' | 'copies'>('trades');
  const [showPnlCard, setShowPnlCard] = useState(false);
  const [pnlCardTrade, setPnlCardTrade] = useState<any>(null);

  // New trade history
  const { data: trades, isLoading: tradesLoading } = useQuery({
    queryKey: ['trade-history', address, marketFilter],
    queryFn: () => getTradeHistory({ limit: 200, ...(marketFilter ? { market_id: marketFilter } : {}) }),
    enabled: !!address && tab === 'trades',
    refetchInterval: 15000,
  });

  // Order history
  const { data: orderHistoryData, isLoading: orderHistLoading } = useQuery({
    queryKey: ['order-history-page', address, marketFilter],
    queryFn: () => getOrderHistory({ limit: 200, ...(marketFilter ? { market_id: marketFilter } : {}) }),
    enabled: !!address && tab === 'order_history',
    refetchInterval: 15000,
  });

  // Legacy copy trade history
  const { data: legacyOrders, isLoading: legacyLoading } = useQuery({
    queryKey: ['order-history', address, marketFilter],
    queryFn: () =>
      api.get('/api/orders/history', { params: { limit: 200, ...(marketFilter ? { market_id: marketFilter } : {}) } })
        .then((r) => r.data),
    enabled: !!address && tab === 'copies',
    refetchInterval: 15000,
  });

  if (!hydrated) {
    return <div className="flex justify-center py-12"><div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" /></div>;
  }

  if (!address) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-bold text-text-primary">Order History</h1>
        <div className="card text-center py-12 text-sm text-text-secondary">Connect your wallet to view order history</div>
      </div>
    );
  }

  const isLoading = tab === 'trades' ? tradesLoading : tab === 'order_history' ? orderHistLoading : legacyLoading;
  const data = tab === 'trades' ? trades : tab === 'order_history' ? orderHistoryData : legacyOrders;

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-text-primary">Order History</h1>
        <div className="flex items-center gap-2">
          {/* Tabs */}
          <div className="flex bg-bg-secondary rounded-lg p-0.5">
            <button
              onClick={() => setTab('trades')}
              className={clsx('text-xs px-3 py-1 rounded-md font-medium transition-colors',
                tab === 'trades' ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}
            >
              Trades
            </button>
            <button
              onClick={() => setTab('order_history')}
              className={clsx('text-xs px-3 py-1 rounded-md font-medium transition-colors',
                tab === 'order_history' ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}
            >
              Order History
            </button>
            <button
              onClick={() => setTab('copies')}
              className={clsx('text-xs px-3 py-1 rounded-md font-medium transition-colors',
                tab === 'copies' ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary')}
            >
              Copy Trades
            </button>
          </div>
          {/* Market filter */}
          <select
            value={marketFilter ?? ''}
            onChange={(e) => setMarketFilter(e.target.value ? Number(e.target.value) : null)}
            className="text-xs bg-bg-secondary border border-text-secondary/20 rounded-lg px-2 py-1.5 text-text-primary"
          >
            <option value="">All Markets</option>
            {Object.entries(MARKETS).map(([id, m]) => (
              <option key={id} value={id}>{m.symbol}</option>
            ))}
          </select>
          {/* PnL Card */}
          <button
            onClick={() => setShowPnlCard(true)}
            className="text-xs px-3 py-1.5 rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors font-medium"
          >
            PnL Card
          </button>
          {/* CSV Export */}
          <button
            onClick={() => {
              api.get('/api/export/trades', { responseType: 'blob' }).then((res) => {
                const url = window.URL.createObjectURL(new Blob([res.data]));
                const a = document.createElement('a');
                a.href = url;
                a.download = `trades_${address?.slice(0, 10)}_${new Date().toISOString().slice(0, 10)}.csv`;
                a.click();
                window.URL.revokeObjectURL(url);
              });
            }}
            className="text-xs px-3 py-1.5 rounded-lg bg-accent/10 text-accent hover:bg-accent/20 transition-colors font-medium"
          >
            Export CSV
          </button>
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="flex justify-center py-12"><LoadingSpinner /></div>
        ) : !data || data.length === 0 ? (
          <div className="text-center py-12 text-sm text-text-secondary">
            {tab === 'trades' ? 'No trades yet. Place an order to see it here.'
              : tab === 'order_history' ? 'No order history yet.'
              : 'No copy trade history'}
          </div>
        ) : tab === 'trades' ? (
          <>
            {/* Trades - Desktop table (Perpl-style) */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-text-secondary border-b border-text-secondary/10">
                    <th className="px-4 py-2.5 text-left font-medium">Time</th>
                    <th className="px-2 py-2.5 text-left font-medium">Coin</th>
                    <th className="px-2 py-2.5 text-left font-medium">Direction</th>
                    <th className="px-2 py-2.5 text-right font-medium">Price</th>
                    <th className="px-2 py-2.5 text-right font-medium">Size</th>
                    <th className="px-2 py-2.5 text-right font-medium">Trade Value</th>
                    <th className="px-2 py-2.5 text-right font-medium">Fee</th>
                    <th className="px-2 py-2.5 text-right font-medium">PnL</th>
                    <th className="px-3 py-2.5 text-left font-medium">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((t: any) => {
                    const cfg = MARKETS[t.market_id];
                    const dec = cfg?.decimals ?? 2;
                    const isClose = t.action === 'close';
                    const direction = `${isClose ? 'Close' : 'Open'} ${t.side === 'long' ? 'Long' : 'Short'}`;
                    const dirColor = t.side === 'long' ? 'text-success' : 'text-danger';
                    return (
                      <tr key={t.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                        <td className="px-4 py-2 text-text-secondary whitespace-nowrap">
                          {t.time ? new Date(t.time).toLocaleString() : '--'}
                        </td>
                        <td className="px-2 py-2 font-bold text-text-primary">{t.symbol}</td>
                        <td className={clsx('px-2 py-2 font-semibold', dirColor)}>{direction}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{formatPrice(t.price, dec)}</td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {t.size != null ? t.size.toLocaleString(undefined, { maximumFractionDigits: cfg?.sizeDecimals ?? 4 }) : '--'}
                        </td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {t.notional != null ? formatUSD(t.notional) : '--'}
                        </td>
                        <td className="px-2 py-2 text-right text-text-secondary">
                          {t.fee != null ? formatUSD(t.fee) : '$0.00'}
                        </td>
                        <td className="px-2 py-2 text-right">
                          {isClose && t.pnl != null ? (
                            <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                              {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                            </span>
                          ) : '--'}
                        </td>
                        <td className="px-3 py-2">
                          <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium',
                            t.source === 'copy_trade' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary'
                          )}>
                            {t.source === 'copy_trade' ? 'Social' : 'Trade'}
                          </span>
                        </td>
                        <td className="px-2 py-2">
                          {t.pnl != null && (
                            <button onClick={() => setPnlCardTrade({ ...t, direction: `${t.action === 'close' ? 'Close' : 'Open'} ${t.side === 'long' ? 'Long' : 'Short'}` })} className="text-[9px] px-2 py-0.5 rounded bg-accent/10 text-accent hover:bg-accent/20 font-medium">
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

            {/* Trades - Mobile cards */}
            <div className="md:hidden">
              {data.map((t: any) => {
                const cfg = MARKETS[t.market_id];
                const dec = cfg?.decimals ?? 2;
                const isClose = t.action === 'close';
                const direction = `${isClose ? 'Close' : 'Open'} ${t.side === 'long' ? 'Long' : 'Short'}`;
                const dirColor = t.side === 'long' ? 'text-success' : 'text-danger';
                return (
                  <div key={t.id} className="px-3 py-2.5 border-t border-text-secondary/5 text-[11px]">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-text-primary">{t.symbol}</span>
                        <span className={clsx('font-semibold', dirColor)}>{direction}</span>
                        <span className={clsx('text-[9px] px-1.5 py-0.5 rounded font-medium',
                          t.source === 'copy_trade' ? 'bg-accent/10 text-accent' : 'bg-text-secondary/10 text-text-secondary'
                        )}>
                          {t.source === 'copy_trade' ? 'Social' : 'Trade'}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                      {isClose && t.pnl != null ? (
                        <>
                          <span className={clsx('font-bold', t.pnl >= 0 ? 'text-success' : 'text-danger')}>
                            {t.pnl >= 0 ? '+' : ''}{formatUSD(t.pnl)}
                          </span>
                          <button onClick={() => setPnlCardTrade({ ...t, direction })} className="text-[9px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">Share</button>
                        </>
                      ) : (
                        <span className="text-text-primary font-bold">{t.notional != null ? formatUSD(t.notional) : '--'}</span>
                      )}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                      <span>{t.time ? new Date(t.time).toLocaleString() : '--'}</span>
                      <span>Price: {formatPrice(t.price, dec)}</span>
                      <span>Size: {t.size?.toLocaleString(undefined, { maximumFractionDigits: cfg?.sizeDecimals ?? 4 })}</span>
                      <span>Value: {t.notional != null ? formatUSD(t.notional) : '--'}</span>
                      <span>Fee: {t.fee != null ? formatUSD(t.fee) : '$0.00'}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : tab === 'order_history' ? (
          <>
            {/* Order History - Desktop */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-text-secondary border-b border-text-secondary/10">
                    <th className="px-4 py-2.5 text-left font-medium">Time</th>
                    <th className="px-2 py-2.5 text-left font-medium">Type</th>
                    <th className="px-2 py-2.5 text-left font-medium">Coin</th>
                    <th className="px-2 py-2.5 text-left font-medium">Direction</th>
                    <th className="px-2 py-2.5 text-right font-medium">Size</th>
                    <th className="px-2 py-2.5 text-right font-medium">Filled Size</th>
                    <th className="px-2 py-2.5 text-right font-medium">Order Value</th>
                    <th className="px-2 py-2.5 text-right font-medium">Price</th>
                    <th className="px-2 py-2.5 text-right font-medium">Fill Price</th>
                    <th className="px-2 py-2.5 text-left font-medium">Reduce Only</th>
                    <th className="px-2 py-2.5 text-right font-medium">Fee</th>
                    <th className="px-2 py-2.5 text-right font-medium">PnL</th>
                    <th className="px-2 py-2.5 text-left font-medium">Status</th>
                    <th className="px-3 py-2.5 text-right font-medium">Order ID</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((o: any) => {
                    const cfg = MARKETS[o.market_id];
                    const dec = cfg?.decimals ?? 2;
                    const dirColor = o.direction?.includes('Long') ? 'text-success' : 'text-danger';
                    const stColor = o.status === 'filled' ? 'text-success' : o.status === 'failed' ? 'text-danger' : o.status === 'open' ? 'text-accent' : o.status === 'cancelled' ? 'text-warning' : 'text-text-secondary';
                    return (
                      <tr key={o.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                        <td className="px-4 py-2 text-text-secondary whitespace-nowrap">{o.time ? new Date(o.time).toLocaleString() : '--'}</td>
                        <td className="px-2 py-2 text-text-secondary capitalize">{o.order_type}</td>
                        <td className="px-2 py-2 font-bold text-text-primary">{o.symbol}</td>
                        <td className={clsx('px-2 py-2 font-semibold', dirColor)}>{o.direction}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{o.size?.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{o.filled_size != null && o.filled_size > 0 ? o.filled_size.toLocaleString(undefined, { maximumFractionDigits: 0 }) : '--'}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{o.order_value != null ? formatUSD(o.order_value) : '--'}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{o.price != null ? formatPrice(o.price, dec) : '--'}</td>
                        <td className="px-2 py-2 text-right text-text-primary">{o.fill_price != null ? formatPrice(o.fill_price, dec) : '--'}</td>
                        <td className="px-2 py-2 text-text-secondary">{o.reduce_only ? 'Yes' : 'No'}</td>
                        <td className="px-2 py-2 text-right text-text-secondary">{o.fee != null ? formatUSD(o.fee) : '$0.00'}</td>
                        <td className="px-2 py-2 text-right">
                          {o.pnl != null ? (
                            <span className={clsx('font-bold', o.pnl >= 0 ? 'text-success' : 'text-danger')}>
                              {o.pnl >= 0 ? '+' : ''}{formatUSD(o.pnl)}
                            </span>
                          ) : '--'}
                        </td>
                        <td className={clsx('px-2 py-2 font-semibold capitalize', stColor)}>{o.status}</td>
                        <td className="px-3 py-2 text-right text-text-secondary font-mono text-[10px]">{o.order_id || '--'}</td>
                        <td className="px-2 py-2">
                          {o.pnl != null && (
                            <button onClick={() => setPnlCardTrade(o)} className="text-[9px] px-2 py-0.5 rounded bg-accent/10 text-accent hover:bg-accent/20 font-medium">
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
            {/* Order History - Mobile */}
            <div className="md:hidden">
              {data.map((o: any) => {
                const cfg = MARKETS[o.market_id];
                const dec = cfg?.decimals ?? 2;
                const dirColor = o.direction?.includes('Long') ? 'text-success' : 'text-danger';
                const stColor = o.status === 'filled' ? 'text-success' : o.status === 'failed' ? 'text-danger' : o.status === 'open' ? 'text-accent' : 'text-warning';
                return (
                  <div key={o.id} className="px-3 py-2.5 border-t border-text-secondary/5 text-[11px]">
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
                      {o.filled_size > 0 && <span>Filled: {o.filled_size?.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>}
                      {o.order_value != null && <span>Value: {formatUSD(o.order_value)}</span>}
                      {o.price != null && <span>Price: {formatPrice(o.price, dec)}</span>}
                      {o.fill_price != null && <span>Fill: {formatPrice(o.fill_price, dec)}</span>}
                      {o.fee != null && o.fee > 0 && <span>Fee: {formatUSD(o.fee)}</span>}
                      {o.reduce_only && <span>Reduce Only</span>}
                      {o.order_id && <span className="font-mono">#{o.order_id}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <>
            {/* Legacy copy trade orders - Desktop */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-text-secondary border-b border-text-secondary/10">
                    <th className="px-4 py-2.5 text-left font-medium">Time</th>
                    <th className="px-2 py-2.5 text-left font-medium">Market</th>
                    <th className="px-2 py-2.5 text-left font-medium">Side</th>
                    <th className="px-2 py-2.5 text-left font-medium">Type</th>
                    <th className="px-2 py-2.5 text-right font-medium">Size</th>
                    <th className="px-2 py-2.5 text-right font-medium">Price</th>
                    <th className="px-2 py-2.5 text-right font-medium">Amount</th>
                    <th className="px-2 py-2.5 text-right font-medium">Leverage</th>
                    <th className="px-2 py-2.5 text-left font-medium">Status</th>
                    <th className="px-3 py-2.5 text-left font-medium">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((order: any) => {
                    const cfg = MARKETS[order.market_id];
                    const dec = cfg?.decimals ?? 2;
                    return (
                      <tr key={order.id} className="border-t border-text-secondary/5 hover:bg-bg-secondary/30">
                        <td className="px-4 py-2 text-text-secondary whitespace-nowrap">
                          {order.time ? new Date(order.time).toLocaleString() : '--'}
                        </td>
                        <td className="px-2 py-2">
                          <span className="font-bold text-text-primary">{order.symbol || cfg?.symbol || `M${order.market_id}`}</span>
                        </td>
                        <td className="px-2 py-2">
                          <span className={clsx('font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded',
                            order.side?.includes('long') || order.side?.includes('buy') ? 'text-success bg-success/10' : 'text-danger bg-danger/10'
                          )}>
                            {order.side}
                          </span>
                        </td>
                        <td className="px-2 py-2 text-text-secondary capitalize">{order.type || '--'}</td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {order.size != null ? order.size.toFixed(cfg?.decimals ?? 4) : '--'}
                        </td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {order.price != null ? `$${formatPrice(order.price, dec)}` : '--'}
                        </td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {order.amount_usd != null ? formatUSD(order.amount_usd) : '--'}
                        </td>
                        <td className="px-2 py-2 text-right text-text-primary">
                          {order.leverage != null ? `${order.leverage}x` : '--'}
                        </td>
                        <td className="px-2 py-2">
                          <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium',
                            order.status === 'filled' ? 'text-success bg-success/10' :
                            order.status === 'failed' ? 'text-danger bg-danger/10' :
                            'text-warning bg-warning/10'
                          )}>
                            {order.status}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-[10px] text-text-secondary">{order.source?.replace('_', ' ')}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Legacy - Mobile cards */}
            <div className="md:hidden">
              {data.map((order: any) => {
                const cfg = MARKETS[order.market_id];
                const dec = cfg?.decimals ?? 2;
                return (
                  <div key={order.id} className="px-3 py-2 border-t border-text-secondary/5 text-[11px]">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-text-primary">{order.symbol || cfg?.symbol || `M${order.market_id}`}</span>
                        <span className={clsx('font-semibold uppercase text-[10px] px-1.5 py-0.5 rounded',
                          order.side?.includes('long') || order.side?.includes('buy') ? 'text-success bg-success/10' : 'text-danger bg-danger/10'
                        )}>
                          {order.side}
                        </span>
                        <span className={clsx('text-[10px] px-1.5 py-0.5 rounded font-medium',
                          order.status === 'filled' ? 'text-success bg-success/10' :
                          order.status === 'failed' ? 'text-danger bg-danger/10' :
                          'text-warning bg-warning/10'
                        )}>
                          {order.status}
                        </span>
                      </div>
                      <span className="text-text-primary font-bold">
                        {order.amount_usd != null ? formatUSD(order.amount_usd) : order.price != null ? `$${formatPrice(order.price, dec)}` : '--'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[10px] text-text-secondary">
                      <span>{order.time ? formatTimeAgo(order.time) : '--'}</span>
                      <span className="capitalize">{order.type || '--'}</span>
                      {order.size != null && <span>Size: {order.size.toFixed(cfg?.decimals ?? 4)}</span>}
                      {order.leverage != null && <span>{order.leverage}x</span>}
                      {order.source && <span>{order.source.replace('_', ' ')}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {showPnlCard && (
        <Suspense fallback={null}>
          <PnlCard onClose={() => setShowPnlCard(false)} />
        </Suspense>
      )}

      {pnlCardTrade && (
        <Suspense fallback={null}>
          <TradePnlCard trade={pnlCardTrade} onClose={() => setPnlCardTrade(null)} />
        </Suspense>
      )}
    </div>
  );
}
