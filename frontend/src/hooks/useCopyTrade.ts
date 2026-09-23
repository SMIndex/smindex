import { useState, useCallback, useEffect } from 'react';
import { useAccount, useSignMessage } from 'wagmi';
import { normalizePerplFeeBps } from '@/lib/perplFees';
import { useToast } from '@/components/common/Toast';
import { getTraderPositions, saveTrade, saveOrder } from '@/lib/api';
import {
  getPerplPayload,
  connectPerpl,
  openTradingWs,
  placeOrder,
  closePosition,
  getSession,
  setAccountId,
  tryAutoReconnect,
  warmupTradingConnection,
  MARKET_CONFIGS,
} from '@/lib/perplTrading';

interface CopyParams {
  marketId: number;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  leverage: number;
  entryPrice: number;
  orderMode?: 'market' | 'limit';
  limitPrice?: number;
  postOnly?: boolean;   // limit only: fl:1 guaranteed maker (rejects if it crosses)
  slippageBps?: number; // market only: user slippage, capped at the market's server cap
  source?: 'manual' | 'copy_trade';
}

export function useCopyTrade() {
  const { address, isConnected } = useAccount();
  const { signMessageAsync } = useSignMessage();
  const toast = useToast();
  const [isCopying, setIsCopying] = useState(false);
  const [status, setStatus] = useState('');

  // Pre-connect in the background so the first order is instant (no popup —
  // only uses an enrolled API key or a stored session nonce).
  useEffect(() => {
    if (isConnected && address) warmupTradingConnection(address);
  }, [isConnected, address]);

  const copyPosition = useCallback(async (params: CopyParams) => {
    // Fail LOUDLY (throw) on every non-placement condition — callers must never treat a
    // no-op as a success/fill. Returning undefined here caused phantom "filled" copies.
    if (!isConnected || !address) {
      toast.error('Connect your wallet first');
      throw new Error('Wallet not connected');
    }

    setIsCopying(true);
    setStatus('');

    try {
      let sess = getSession();

      if (!sess.authenticated || !sess.hasWs) {
        // Try auto-reconnect with stored nonce (no wallet popup)
        setStatus('Connecting...');
        const reconnected = await tryAutoReconnect(address);

        if (!reconnected) {
          // Need fresh SIWE sign (first time or nonce expired)
          setStatus('Requesting Perpl auth...');
          const payload = await getPerplPayload(address);

          setStatus('Sign the message in your wallet...');
          const signature = await signMessageAsync({ message: payload.message });

          setStatus('Connecting to Perpl...');
          const nonce = await connectPerpl(address, payload, signature);

          setStatus('Opening trading connection...');
          await openTradingWs(nonce, address);

          setStatus('Fetching account...');
          try {
            const detail = await getTraderPositions(address);
            if (detail?.account_id) setAccountId(detail.account_id);
          } catch {}
        }
      }

      const session = getSession();
      if (!session.accountId) {
        toast.error('No Perpl exchange account found. Create one at perpl.xyz and deposit funds first.');
        throw new Error('No Perpl exchange account found');
      }

      const mcfg = MARKET_CONFIGS[params.marketId];
      if (!mcfg) {
        toast.error(`Unknown market ${params.marketId}`);
        throw new Error(`Unknown market ${params.marketId}`);
      }

      const isLimit = params.orderMode === 'limit' && params.limitPrice;
      const direction = `Open ${params.side === 'long' ? 'Long' : 'Short'}`;
      const orderPrice = isLimit ? params.limitPrice! : params.entryPrice;
      const orderValue = params.size * orderPrice;
      const src = params.source || 'manual';

      setStatus(`Placing ${isLimit ? 'limit' : 'market'} ${params.side} ${params.symbol}...`);

      let result: any;
      try {
        result = await placeOrder({
          marketId: params.marketId,
          side: params.side,
          size: params.size,
          leverage: params.leverage,
          price: params.entryPrice,
          sizeDecimals: mcfg.sizeDecimals,
          priceDecimals: mcfg.priceDecimals,
          orderMode: params.orderMode,
          limitPrice: params.limitPrice,
          postOnly: params.postOnly,
          slippageBps: params.slippageBps,
        });
      } catch (orderErr: any) {
        // Order failed/rejected — save to order history as failed
        try {
          await saveOrder({
            market_id: params.marketId, symbol: params.symbol, direction,
            order_type: isLimit ? 'limit' : 'market', size: params.size,
            order_value: Math.round(orderValue * 100) / 100,
            price: orderPrice, status: 'failed', source: src,
            error: orderErr?.message || 'Order failed',
            raw_response: null,
          });
        } catch {}
        throw orderErr;
      }

      // Determine outcome
      const filled = result?.filled || result?.filledSize > 0;
      const placedInBook = result?.placed && !filled;
      const fillPrice = result?.filledPrice
        ? result.filledPrice / (10 ** mcfg.priceDecimals)
        : params.entryPrice;
      const fillSize = result?.filledSize
        ? result.filledSize / (10 ** mcfg.sizeDecimals)
        : params.size;
      const notional = fillSize * fillPrice;
      const fee = notional * (normalizePerplFeeBps(mcfg.takerFeeBps) / 10000);
      const orderId = result?.orderId?.toString() || null;
      // Never default to 'filled' — an unconfirmed result is 'unconfirmed', not a fill.
      const status = filled ? 'filled' : placedInBook ? 'open' : 'unconfirmed';

      const label = isLimit ? 'Limit' : 'Market';
      toast.success(`${label} ${params.side.toUpperCase()} ${params.symbol} ${params.leverage}x placed`);
      setStatus('Order placed');

      // Save to order history (every attempt)
      try {
        await saveOrder({
          market_id: params.marketId, symbol: params.symbol, direction,
          order_type: isLimit ? 'limit' : 'market', size: params.size,
          filled_size: filled ? fillSize : placedInBook ? 0 : fillSize,
          order_value: Math.round(orderValue * 100) / 100,
          price: orderPrice, fill_price: filled ? fillPrice : undefined,
          reduce_only: false, status, order_id: orderId,
          fee: filled ? Math.round(fee * 100) / 100 : 0,
          source: src, raw_response: result || null,
        });
      } catch (e) {
        console.warn('[order-history] failed to save:', e);
      }

      // Save to trade history (only confirmed fills)
      if (filled) {
        try {
          await saveTrade({
            market_id: params.marketId, symbol: params.symbol,
            side: params.side, action: 'open',
            order_type: isLimit ? 'limit' : 'market',
            size: fillSize, price: fillPrice, leverage: params.leverage,
            fee: Math.round(fee * 100) / 100,
            notional: Math.round(notional * 100) / 100,
            order_id: result?.orderId || null,
            raw_response: result || null, source: src,
          });
        } catch (e) {
          console.warn('[trade-history] failed to save:', e);
        }
      }

      // Trigger immediate position refresh across all components
      setTimeout(() => window.dispatchEvent(new CustomEvent('order_placed')), 2000);
      setTimeout(() => window.dispatchEvent(new CustomEvent('order_placed')), 5000);

      return result;

    } catch (err: any) {
      const msg = err?.message || 'Order failed';
      toast.error(msg);
      setStatus('');
      throw err;
    } finally {
      setIsCopying(false);
    }
  }, [address, isConnected, signMessageAsync, toast]);

  // Close/reduce a live copied position via the SAME client wallet-signed path the
  // terminal uses (perplTrading.closePosition). Additive — copyPosition is untouched.
  const closeLivePosition = useCallback(async (params: {
    marketId: number; side: 'long' | 'short'; size: number; markPrice: number;
  }) => {
    if (!isConnected || !address) {
      toast.error('Connect your wallet first');
      throw new Error('wallet not connected');
    }
    const mcfg = MARKET_CONFIGS[params.marketId];
    if (!mcfg) throw new Error('Market config unavailable');

    setIsCopying(true);
    setStatus('');
    try {
      let sess = getSession();
      if (!sess.authenticated || !sess.hasWs) {
        setStatus('Connecting...');
        const reconnected = await tryAutoReconnect(address);
        if (!reconnected) {
          setStatus('Requesting Perpl auth...');
          const payload = await getPerplPayload(address);
          setStatus('Sign the message in your wallet...');
          const signature = await signMessageAsync({ message: payload.message });
          setStatus('Connecting to Perpl...');
          const nonce = await connectPerpl(address, payload, signature);
          setStatus('Opening trading connection...');
          await openTradingWs(nonce, address);
          try {
            const detail = await getTraderPositions(address);
            if (detail?.account_id) setAccountId(detail.account_id);
          } catch {}
        }
      }
      if (!getSession().accountId) {
        toast.error('No Perpl exchange account found.');
        throw new Error('no account');
      }
      setStatus(`Closing ${params.side} ${params.marketId}...`);
      const result = await closePosition({
        marketId: params.marketId, side: params.side, size: params.size,
        markPrice: params.markPrice,
        priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
      });
      setStatus('Close order placed');
      setTimeout(() => window.dispatchEvent(new CustomEvent('order_placed')), 2000);
      return result;
    } finally {
      setIsCopying(false);
    }
  }, [address, isConnected, signMessageAsync, toast]);

  return {
    copyPosition,
    closeLivePosition,
    isCopying,
    status,
  };
}
