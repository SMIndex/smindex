import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSignMessage } from 'wagmi';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import { getTraderPositions, getUsernames, saveTrade } from '@/lib/api';
import { useCopyStore } from '@/stores/copyStore';
import {
  closePosition, getSession, getPerplPayload, connectPerpl,
  openTradingWs, setAccountId, tryAutoReconnect, MARKET_CONFIGS,
} from '@/lib/perplTrading';
import { formatUSD, formatPrice, formatCompact, shortenAddress, displayName } from '@/lib/formatters';
import { useToast } from '@/components/common/Toast';
import PriceChart from '@/components/copytrade/PriceChart';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function MyPositions() {
  const { address, hydrated } = useAuth();
  const copies = useCopyStore((s) => s.copies);
  const getCopySource = useCopyStore((s) => s.getCopySource);
  const removeCopy = useCopyStore((s) => s.removeCopy);
  const toast = useToast();
  const { signMessageAsync } = useSignMessage();
  const [closingMarket, setClosingMarket] = useState<number | null>(null);
  const [expandedMarket, setExpandedMarket] = useState<number | null>(null);
  const [closeStatus, setCloseStatus] = useState('');

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['my-positions', address],
    queryFn: () => getTraderPositions(address!),
    enabled: !!address,
    refetchInterval: 5000,
  });

  // Collect unique leader wallets from copies to resolve usernames
  const leaderWallets = useMemo(() => {
    const wallets = new Set<string>();
    copies.forEach((c) => { if (c.copied_from) wallets.add(c.copied_from.toLowerCase()); });
    return [...wallets];
  }, [copies]);

  const { data: usernameMap = {} } = useQuery({
    queryKey: ['usernames', leaderWallets.join(',')],
    queryFn: () => getUsernames(leaderWallets),
    enabled: leaderWallets.length > 0,
    staleTime: 60000,
  });

  const ensureConnected = async (): Promise<boolean> => {
    const sess = getSession();
    if (sess.authenticated && sess.hasWs) return true;
    if (!address) return false;

    try {
      // Try auto-reconnect first (no wallet popup)
      setCloseStatus('Connecting...');
      const reconnected = await tryAutoReconnect(address);
      if (reconnected) { setCloseStatus(''); return true; }

      // Need fresh SIWE sign
      setCloseStatus('Authenticating with Perpl...');
      const payload = await getPerplPayload(address);

      setCloseStatus('Sign the message in your wallet...');
      const signature = await signMessageAsync({ message: payload.message });

      setCloseStatus('Connecting...');
      const nonce = await connectPerpl(address, payload, signature);

      setCloseStatus('Opening trading connection...');
      await openTradingWs(nonce, address);

      try {
        const detail = await getTraderPositions(address);
        if (detail?.account_id) setAccountId(detail.account_id);
      } catch {}

      setCloseStatus('');
      return true;
    } catch (err: any) {
      toast.error(err?.message || 'Failed to connect to Perpl');
      setCloseStatus('');
      return false;
    }
  };

  const handleClose = async (pos: any) => {
    const connected = await ensureConnected();
    if (!connected) return;

    setClosingMarket(pos.market_id);
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
      toast.success(`Closed ${pos.symbol} ${pos.side.toUpperCase()} position`);

      // Save close trade to history
      try {
        const closePrice = pos.mark_price;
        const closePnl = pos.side === 'long'
          ? (closePrice - pos.entry_price) * pos.size
          : (pos.entry_price - closePrice) * pos.size;
        const notional = pos.size * closePrice;
        await saveTrade({
          market_id: pos.market_id,
          symbol: pos.symbol,
          side: pos.side,
          action: 'close',
          order_type: 'market',
          size: pos.size,
          price: closePrice,
          leverage: pos.leverage,
          notional: Math.round(notional * 100) / 100,
          pnl: Math.round(closePnl * 100) / 100,
          source: 'copy_trade',
        });
      } catch (e) {
        console.warn('[trade-history] failed to save copy close:', e);
      }

      removeCopy(pos.market_id);
      refetch();
    } catch (err: any) {
      toast.error(err?.message || 'Failed to close position');
    } finally {
      setClosingMarket(null);
    }
  };

  if (!hydrated) {
    return <div className="flex justify-center py-12"><div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" /></div>;
  }

  if (!address) {
    return (
      <div className="card text-center py-8 text-sm text-text-secondary">
        Connect your wallet to see your positions
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card text-center py-8 text-sm text-text-secondary">
        No Perpl account found for this wallet
      </div>
    );
  }

  const hasPositions = data.positions?.length > 0;
  const hasOrders = data.orders?.length > 0;

  return (
    <div className="space-y-4">
      {/* Account summary */}
      <div className="card">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Account</div>
            <div className="text-base sm:text-lg font-bold text-text-primary">#{data.account_id}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Balance</div>
            <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(data.balance)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Margin Used</div>
            <div className="text-base sm:text-lg font-bold text-text-primary truncate">{formatUSD(data.margin_used)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">Available</div>
            <div className="text-base sm:text-lg font-bold text-success truncate">{formatUSD(Math.max(0, data.balance - data.margin_used))}</div>
          </div>
        </div>
      </div>

      {/* Open Positions */}
      <div className="card p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">
            Open Positions ({data.position_count})
          </h2>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
            <span className="text-[10px] text-text-secondary">
              {closeStatus || 'Live — updates every 5s'}
            </span>
          </div>
        </div>

        {hasPositions ? (
          <div className="divide-y divide-text-secondary/5">
            {data.positions.map((pos: any) => {
              const mcfg = MARKET_CONFIGS[pos.market_id];
              const priceDec = mcfg?.priceDecimals ?? 2;
              const mmrFraction = 100 / (mcfg?.maintenanceMarginHdths ?? 2000);
              const copySource = getCopySource(pos.market_id);
              const isClosing = closingMarket === pos.market_id;
              const isExpanded = expandedMarket === pos.market_id;

              const mmr = pos.notional * mmrFraction;
              const liqPrice = pos.side === 'long'
                ? pos.entry_price - (pos.deposit - mmr) / pos.size
                : pos.entry_price + (pos.deposit - mmr) / pos.size;
              const liqDistancePct = pos.mark_price > 0
                ? Math.abs(liqPrice - pos.mark_price) / pos.mark_price * 100 : 0;
              const roe = pos.deposit > 0 ? (pos.pnl / pos.deposit) * 100 : 0;

              return (
                <div key={pos.market_id}>
                  <div
                    className="p-4 hover:bg-bg-secondary/30 transition-colors cursor-pointer"
                    onClick={() => setExpandedMarket(isExpanded ? null : pos.market_id)}
                  >
                    {/* Header */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className={clsx(
                            'text-[10px] transition-transform',
                            isExpanded && 'rotate-90',
                          )}
                        >
                          {'\u25B6'}
                        </span>
                        <span className="text-lg font-bold text-text-primary">{pos.symbol}</span>
                        <span
                          className={clsx(
                            'font-semibold uppercase px-2 py-0.5 rounded text-xs',
                            pos.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10',
                          )}
                        >
                          {pos.side} {pos.leverage}x
                        </span>
                        {copySource && (
                          <span className="text-[10px] text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                            Copied from {displayName(usernameMap[copySource.toLowerCase()], copySource)}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="text-right">
                          <div
                            className={clsx(
                              'text-lg font-bold',
                              pos.pnl >= 0 ? 'text-success' : 'text-danger',
                            )}
                          >
                            {pos.pnl >= 0 ? '+' : ''}{formatUSD(pos.pnl)}
                          </div>
                          <div
                            className={clsx(
                              'text-xs font-medium',
                              roe >= 0 ? 'text-success' : 'text-danger',
                            )}
                          >
                            ROE: {roe >= 0 ? '+' : ''}{roe.toFixed(2)}%
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleClose(pos);
                          }}
                          disabled={isClosing}
                          className={clsx(
                            'px-3 py-1.5 min-h-[44px] rounded text-xs font-semibold transition-colors',
                            isClosing
                              ? 'bg-bg-card text-text-secondary cursor-wait'
                              : 'bg-danger text-white hover:bg-danger/80',
                          )}
                        >
                          {isClosing ? 'Closing...' : 'Close'}
                        </button>
                      </div>
                    </div>

                    {/* Stats grid */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 text-xs">
                      <div>
                        <div className="text-text-secondary mb-0.5">Size</div>
                        <div className="text-text-primary font-medium">{pos.size.toFixed(mcfg?.sizeDecimals ?? 4)} {pos.symbol}</div>
                      </div>
                      <div>
                        <div className="text-text-secondary mb-0.5">Entry</div>
                        <div className="text-text-primary font-medium">${formatPrice(pos.entry_price, priceDec)}</div>
                      </div>
                      <div>
                        <div className="text-text-secondary mb-0.5">Mark</div>
                        <div className="text-text-primary font-medium">${formatPrice(pos.mark_price, priceDec)}</div>
                      </div>
                      <div>
                        <div className="text-text-secondary mb-0.5">Margin</div>
                        <div className="text-text-primary font-medium">{formatUSD(pos.deposit)}</div>
                      </div>
                      <div>
                        <div className="text-text-secondary mb-0.5">Funding</div>
                        <div className={clsx('font-medium', pos.funding_pnl >= 0 ? 'text-success' : 'text-danger')}>
                          {pos.funding_pnl >= 0 ? '+' : ''}{formatUSD(pos.funding_pnl)}
                        </div>
                      </div>
                      <div>
                        <div className="text-text-secondary mb-0.5">Liq. Price</div>
                        <div className="text-danger font-bold">
                          ${formatPrice(liqPrice, priceDec)}
                          <span className="text-text-secondary font-normal text-[10px] ml-1">({liqDistancePct.toFixed(1)}%)</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Expanded: Chart */}
                  {isExpanded && (
                    <div className="border-t border-text-secondary/5">
                      <PriceChart
                        marketId={pos.market_id}
                        symbol={pos.symbol}
                        entryPrice={pos.entry_price}
                        liqPrice={liqPrice}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="text-sm text-text-secondary text-center py-8">
            No open positions
          </div>
        )}
      </div>

      {/* Pending Orders */}
      {hasOrders && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">
              Pending Orders ({data.order_count})
            </h2>
          </div>
          <div className="divide-y divide-text-secondary/5">
            {data.orders.map((ord: any, i: number) => (
              <div key={i} className="flex flex-wrap items-center gap-2 sm:gap-4 px-4 py-3 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-bold text-text-primary">{ord.symbol}</span>
                  <span
                    className={clsx(
                      'font-semibold uppercase px-1.5 py-0.5 rounded text-[10px]',
                      ord.side === 'buy' ? 'text-success bg-success/10' :
                      ord.side === 'sell' ? 'text-danger bg-danger/10' :
                      'text-warning bg-warning/10',
                    )}
                  >
                    {ord.side}
                  </span>
                </div>
                <div>
                  <span className="text-text-secondary">Type: </span>
                  <span className="text-text-primary capitalize">{ord.order_type.replace('_', ' ')}</span>
                </div>
                <div>
                  <span className="text-text-secondary">Margin: </span>
                  <span className="text-warning font-medium">{formatUSD(ord.margin_locked)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Copy history */}
      {copies.length > 0 && (
        <div className="card p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-text-secondary/10">
            <h2 className="text-sm font-semibold text-text-primary">Copy Trade History</h2>
          </div>
          <div className="divide-y divide-text-secondary/5">
            {copies.map((c, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 sm:gap-4 px-4 py-3 text-xs">
                <span className="font-bold text-text-primary">{c.symbol}</span>
                <span
                  className={clsx(
                    'font-semibold uppercase px-1.5 py-0.5 rounded text-[10px]',
                    c.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10',
                  )}
                >
                  {c.side} {c.leverage}x
                </span>
                <span className="text-text-secondary">{formatUSD(c.amount_usd)}</span>
                <span className="text-accent text-[10px]">from {displayName(usernameMap[c.copied_from?.toLowerCase()], c.copied_from)}</span>
                <span className="text-text-secondary/50 text-[10px] ml-auto">
                  {new Date(c.timestamp).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
