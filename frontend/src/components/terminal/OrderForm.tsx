import { useState, useEffect } from 'react';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useMarketStore } from '@/stores/marketStore';
import { useTradingStore } from '@/stores/tradingStore';
import { useCopyTrade } from '@/hooks/useCopyTrade';
import { MARKETS } from '@/config/constants';
import { MARKET_CONFIGS, placeSlTpOrder } from '@/lib/perplTrading';
import { getOrderFeeRate, calculateEstimatedFee } from '@/lib/perplFees';
import { formatUSD, formatPrice } from '@/lib/formatters';

type OrderMode = 'market' | 'limit';

// `variant` is presentation ONLY (the action-button label). It never touches
// handlers, validation, or the submit/signing path — shell A passes nothing.
export default function OrderForm({ marketId, variant }: { marketId: number; variant?: 'b' }) {
  const { isConnected } = useAuth();
  const { copyPosition, isCopying, status } = useCopyTrade();
  const markets = useMarketStore((s) => s.markets);
  const limitPriceFromBook = useTradingStore((s) => s.limitPrice);
  const setLimitPrice = useTradingStore((s) => s.setLimitPrice);
  const stagedOrder = useTradingStore((s) => s.stagedOrder);
  const setStagedOrder = useTradingStore((s) => s.setStagedOrder);
  const [stagedFlash, setStagedFlash] = useState<{ rationale: string | null } | null>(null);

  const market = markets[marketId];
  const mcfg = MARKET_CONFIGS[marketId];
  const config = MARKETS[marketId];

  const [mode, setMode] = useState<OrderMode>('market');
  const [postOnly, setPostOnly] = useState(false);
  const [side, setSide] = useState<'long' | 'short'>('long');
  const [amount, setAmount] = useState(10);
  const [leverage, setLeverage] = useState(5);
  const [price, setPrice] = useState(0);
  const [slPrice, setSlPrice] = useState<number | ''>('');
  const [tpPrice, setTpPrice] = useState<number | ''>('');
  const [error, setError] = useState('');

  // Sync limit price from order book click
  useEffect(() => {
    if (limitPriceFromBook !== null) {
      setPrice(limitPriceFromBook);
      setMode('limit');
      setLimitPrice(null);
    }
  }, [limitPriceFromBook, setLimitPrice]);

  // Consume a staged order from the MCP server (Claude → deep link → Trade.tsx → store)
  // when this OrderForm is mounted on the matching market.
  useEffect(() => {
    if (!stagedOrder) return;
    if (stagedOrder.marketId !== marketId) return;
    setMode(stagedOrder.mode);
    setSide(stagedOrder.side);
    setAmount(stagedOrder.amount);
    setLeverage(stagedOrder.leverage);
    if (stagedOrder.price !== null) setPrice(stagedOrder.price);
    setSlPrice(stagedOrder.sl ?? '');
    setTpPrice(stagedOrder.tp ?? '');
    setStagedFlash({ rationale: stagedOrder.rationale });
    setStagedOrder(null);
    const t = setTimeout(() => setStagedFlash(null), 8000);
    return () => clearTimeout(t);
  }, [stagedOrder, marketId, setStagedOrder]);

  if (!market || !mcfg || !config) return null;

  const maxLev = mcfg.maxLeverage;
  const markPrice = market.mark_price;
  const bidPrice = market.bid_price;
  const askPrice = market.ask_price;
  const isLimit = mode === 'limit';
  // Fill price: limit price for limit orders; ask for market long / bid for market short.
  const fillPrice = isLimit ? price : (side === 'long' ? askPrice : bidPrice);
  const notional = amount * leverage;
  const size = fillPrice > 0 ? notional / fillPrice : 0;

  // Fee — single source of truth in lib/perplFees. Market = taker (IOC). Limit is
  // GTC by default (can cross → taker), so it shows the worst-case taker estimate;
  // with Post-Only ON (fl:1) it is a guaranteed maker. Perpl charges the OPEN side
  // only (close = 0), so this order-open estimate is the full trader-facing fee.
  const feeRate = getOrderFeeRate(mcfg, { isLimit, postOnly: isLimit && postOnly });
  const fee = calculateEstimatedFee(notional, feeRate.bps);
  const feeLabel = feeRate.kind === 'estimate' ? 'est. taker' : feeRate.kind;

  const mmrFraction = 100 / mcfg.maintenanceMarginHdths;
  const mmr = notional * mmrFraction;
  let liqPrice = 0;
  if (size > 0) {
    liqPrice = side === 'long'
      ? fillPrice - (amount - mmr) / size
      : fillPrice + (amount - mmr) / size;
  }
  const liqDist = markPrice > 0 ? Math.abs(liqPrice - markPrice) / markPrice * 100 : 0;

  const handleSubmit = async () => {
    setError('');
    if (!isConnected) { setError('Connect wallet first'); return; }
    if (amount <= 0) { setError('Amount must be > 0'); return; }
    if (mode === 'limit' && price <= 0) { setError('Set a limit price'); return; }

    if (slPrice) {
      if (side === 'long' && slPrice >= fillPrice) { setError('SL must be below entry for longs'); return; }
      if (side === 'short' && slPrice <= fillPrice) { setError('SL must be above entry for shorts'); return; }
    }
    if (tpPrice) {
      if (side === 'long' && tpPrice <= fillPrice) { setError('TP must be above entry for longs'); return; }
      if (side === 'short' && tpPrice >= fillPrice) { setError('TP must be below entry for shorts'); return; }
    }

    try {
      const orderPayload = {
        marketId,
        symbol: config.symbol,
        side,
        size,
        leverage,
        entryPrice: markPrice,
        ...(mode === 'limit' ? { orderMode: 'limit' as const, limitPrice: price, postOnly } : {}),
      };
      // Dev-only submit-payload log so shell A vs shell B can be diffed and proven
      // identical (same component, same path). Inert in production builds.
      if ((import.meta as any).env?.DEV) {
        // eslint-disable-next-line no-console
        console.log('[order-submit]', variant === 'b' ? 'shellB' : 'shellA', JSON.stringify(orderPayload));
      }
      const result = await copyPosition(orderPayload);

      // Attach SL/TP as native Perpl trigger orders (server-side, survive the
      // browser closing). Market entries only: the IOC fill just confirmed, so
      // the position exists. Limit entries may fill later — set SL/TP from the
      // Positions panel once filled. Failures are surfaced, never swallowed.
      const mc = MARKET_CONFIGS[marketId];
      if (mc && (slPrice || tpPrice)) {
        if (mode === 'limit') {
          setError('Order placed. SL/TP are not attached to limit orders — set them in Positions once it fills.');
        } else {
          const filledSize = result?.filledSize ? result.filledSize / (10 ** mc.sizeDecimals) : size;
          const failures: string[] = [];
          for (const [kind, trig] of [['sl', slPrice], ['tp', tpPrice]] as [('sl' | 'tp'), number | ''][]) {
            if (!trig) continue;
            try {
              await placeSlTpOrder({
                marketId, side, orderType: kind, triggerPrice: Number(trig),
                size: filledSize, priceDecimals: mc.priceDecimals, sizeDecimals: mc.sizeDecimals,
              });
            } catch (e: any) {
              failures.push(`${kind.toUpperCase()} failed: ${e?.message || 'unknown error'}`);
            }
          }
          if (failures.length) {
            setError(`Order filled, but ${failures.join(' · ')}`);
            return; // keep the SL/TP inputs so the user can retry from Positions
          }
        }
      }

      setSlPrice('');
      setTpPrice('');
    } catch (err: any) {
      setError(err?.message || 'Order failed');
    }
  };

  const inpStyle: React.CSSProperties = { background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text)' };

  return (
    <div className="rd-sans flex flex-col h-full">
      {stagedFlash && (
        <div className="mx-[18px] mt-3 px-3 py-2 rounded-[11px] animate-pulse-once" style={{ background: 'var(--accent-soft)', border: '1px solid var(--border)' }}>
          <div className="text-[11px] font-semibold" style={{ color: 'var(--accent-2)' }}>Staged by Claude (MCP)</div>
          {stagedFlash.rationale && <div className="text-[10px] mt-0.5 leading-tight" style={{ color: 'var(--dim)' }}>{stagedFlash.rationale}</div>}
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--dim)' }}>Review every field below, then click Place. Nothing has been sent yet.</div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto rd-scroll flex flex-col gap-[13px]" style={{ padding: 18 }}>
        <div className="rd-mono font-bold text-[14px]" style={{ color: 'var(--text)' }}>Place Order</div>

        {/* Order type segmented */}
        <div className="grid grid-cols-2 gap-1.5 rounded-[12px] p-1" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          {(['market', 'limit'] as OrderMode[]).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className="py-2.5 rounded-[9px] text-[12.5px] font-bold capitalize transition-colors"
              style={mode === m ? { background: 'var(--accent-soft)', color: 'var(--accent-2)' } : { color: 'var(--dim)' }}>
              {m}
            </button>
          ))}
        </div>

        {/* Direction segmented */}
        <div className="grid grid-cols-2 gap-1.5 rounded-[12px] p-1" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
          <button onClick={() => setSide('long')} className="py-2.5 rounded-[9px] text-[12.5px] font-bold transition-colors"
            style={side === 'long' ? { background: 'var(--green)', color: '#04130c' } : { color: 'var(--dim)' }}>Long</button>
          <button onClick={() => setSide('short')} className="py-2.5 rounded-[9px] text-[12.5px] font-bold transition-colors"
            style={side === 'short' ? { background: 'var(--red)', color: '#fff' } : { color: 'var(--dim)' }}>Short</button>
        </div>

        {/* Limit price */}
        {isLimit && (
          <div>
            <div className="text-[11.5px] font-semibold mb-1.5" style={{ color: 'var(--dim)' }}>Limit Price</div>
            <input type="number" value={price || ''} onChange={(e) => setPrice(Number(e.target.value))}
              placeholder={formatPrice(markPrice, config.decimals)} step={1 / (10 ** config.decimals)}
              className="w-full rounded-[12px] rd-mono font-semibold text-[15px] outline-none transition-all px-3.5 py-3 focus:border-accent" style={inpStyle} />
            {/* Post-Only: fl:1 — guaranteed maker fee, but rejects if it would cross the book */}
            <button onClick={() => setPostOnly((v) => !v)}
              className="mt-2 w-full flex items-center justify-between px-3 py-2 rounded-[11px] text-[11.5px] transition-colors"
              style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
              <span style={{ color: 'var(--dim)' }}>
                Post-Only <span style={{ color: 'var(--faint)' }}>(maker fee — rejects if it crosses)</span>
              </span>
              <span className="font-bold" style={{ color: postOnly ? 'var(--accent-2)' : 'var(--faint)' }}>{postOnly ? 'ON' : 'OFF'}</span>
            </button>
          </div>
        )}

        {/* Margin */}
        <div>
          <div className="text-[11.5px] font-semibold mb-1.5" style={{ color: 'var(--dim)' }}>Margin (USD)</div>
          <div className="relative">
            <input type="number" value={amount} onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))} min={1} step={5}
              className="w-full rounded-[12px] rd-mono font-semibold text-[15px] outline-none transition-all px-3.5 py-3 focus:border-accent" style={inpStyle} />
            <span className="absolute right-3.5 top-1/2 -translate-y-1/2 rd-mono text-[11px]" style={{ color: 'var(--faint)' }}>USD</span>
          </div>
          <div className="grid grid-cols-5 gap-1.5 mt-2">
            {[5, 10, 25, 50, 100].map((v) => (
              <button key={v} onClick={() => setAmount(v)} className="py-1.5 rounded-[9px] rd-mono text-[11px] font-semibold transition-colors"
                style={amount === v ? { background: 'var(--accent-soft)', color: 'var(--accent-2)', border: '1px solid transparent' } : { background: 'var(--surface-2)', color: 'var(--dim)', border: '1px solid var(--border)' }}>${v}</button>
            ))}
          </div>
        </div>

        {/* Leverage */}
        <div>
          <div className="flex justify-between text-[11.5px] font-semibold mb-1.5">
            <span style={{ color: 'var(--dim)' }}>Leverage</span>
            <span className="rd-mono font-bold" style={{ color: 'var(--accent-2)' }}>{leverage}x</span>
          </div>
          <input type="range" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} min={1} max={maxLev} step={1}
            className="w-full h-[5px] rounded-[3px] appearance-none cursor-pointer" style={{ background: 'var(--surface-2)', accentColor: 'var(--accent)' }} />
          <div className="flex justify-between rd-mono text-[10px] mt-1.5" style={{ color: 'var(--faint)' }}><span>1x</span><span>{maxLev}x</span></div>
        </div>

        {/* SL / TP */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="text-[11px] font-semibold mb-1.5" style={{ color: 'var(--red)' }}>Stop Loss</div>
            <input type="number" value={slPrice} onChange={(e) => setSlPrice(e.target.value ? Number(e.target.value) : '')}
              placeholder={side === 'long' ? 'Below entry' : 'Above entry'} step={1 / (10 ** config.decimals)}
              className="w-full rounded-[11px] rd-mono text-[13px] outline-none transition-all px-3 py-2.5 focus:border-accent" style={inpStyle} />
          </div>
          <div>
            <div className="text-[11px] font-semibold mb-1.5" style={{ color: 'var(--green)' }}>Take Profit</div>
            <input type="number" value={tpPrice} onChange={(e) => setTpPrice(e.target.value ? Number(e.target.value) : '')}
              placeholder={side === 'long' ? 'Above entry' : 'Below entry'} step={1 / (10 ** config.decimals)}
              className="w-full rounded-[11px] rd-mono text-[13px] outline-none transition-all px-3 py-2.5 focus:border-accent" style={inpStyle} />
          </div>
        </div>

        {/* Summary */}
        <div className="rounded-[13px] flex flex-col gap-2.5" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', padding: '13px 15px' }}>
          {[
            [isLimit ? 'Limit Price' : 'Fill Price', `$${formatPrice(fillPrice, config.decimals)}`, false],
            ['Size', `${size.toFixed(mcfg.sizeDecimals)} ${config.symbol}`, false],
            ['Notional', formatUSD(notional), false],
            [`Fee (${feeLabel})`, formatUSD(fee), false],
          ].map(([k, v]) => (
            <div key={k as string} className="flex justify-between text-[12px]">
              <span style={{ color: 'var(--dim)' }}>{k}</span>
              <span className="rd-mono font-semibold" style={{ color: 'var(--text)' }}>{v}</span>
            </div>
          ))}
          <div className="flex justify-between text-[12px] pt-2" style={{ borderTop: '1px solid var(--border)' }}>
            <span style={{ color: 'var(--dim)' }}>Liq. Price</span>
            <span className="rd-mono font-semibold" style={{ color: 'var(--red)' }}>${formatPrice(Math.max(0, liqPrice), config.decimals)} ({liqDist.toFixed(1)}%)</span>
          </div>
          {slPrice !== '' && (
            <div className="flex justify-between text-[12px]"><span style={{ color: 'var(--dim)' }}>Stop Loss</span><span className="rd-mono font-semibold" style={{ color: 'var(--red)' }}>${formatPrice(Number(slPrice), config.decimals)}</span></div>
          )}
          {tpPrice !== '' && (
            <div className="flex justify-between text-[12px]"><span style={{ color: 'var(--dim)' }}>Take Profit</span><span className="rd-mono font-semibold" style={{ color: 'var(--green)' }}>${formatPrice(Number(tpPrice), config.decimals)}</span></div>
          )}
        </div>

        {error && <div className="text-[11px]" style={{ color: 'var(--red)' }}>{error}</div>}
        {status && <div className="text-[11px] animate-pulse" style={{ color: 'var(--accent-2)' }}>{status}</div>}

        {/* Action — pinned to the bottom */}
        <button onClick={handleSubmit} disabled={isCopying || amount <= 0}
          className={clsx('mt-auto rounded-[13px] font-extrabold text-[14px] transition-all', (isCopying || amount <= 0) && 'opacity-50 cursor-not-allowed')}
          style={{
            padding: 15,
            background: side === 'long' ? 'linear-gradient(180deg,#54e29a,var(--green))' : 'linear-gradient(180deg,#ff6f87,var(--red))',
            color: side === 'long' ? '#04130c' : '#fff',
            boxShadow: side === 'long' ? '0 10px 24px -10px var(--green)' : '0 10px 24px -10px var(--red)',
          }}>
          {isCopying ? 'Executing…'
            : variant === 'b'
              ? `${isLimit ? 'Limit' : 'Open'} ${side}, ${formatUSD(amount)} at ${leverage}x`
              : `${isLimit ? 'Limit' : 'Market'} ${side === 'long' ? 'Long' : 'Short'} ${config.symbol}`}
        </button>
      </div>
    </div>
  );
}
