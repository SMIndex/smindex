import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import Modal from '@/components/common/Modal';
import { useCopyTrade } from '@/hooks/useCopyTrade';
import { useCopyStore } from '@/stores/copyStore';
import { useMarketStore } from '@/stores/marketStore';
import { useAuth } from '@/hooks/useAuth';
import { formatUSD, formatPrice } from '@/lib/formatters';
import { MARKET_CONFIGS, placeSlTpOrder } from '@/lib/perplTrading';
import { COPY_LIVE_ENABLED } from '@/config/constants';
import api, { getAccountHealth } from '@/lib/api';

interface CopyModalProps {
  position: {
    market_id: number;
    symbol: string;
    side: 'long' | 'short';
    entry_price: number;
    mark_price: number;
    leverage: number;
    size: number;
  };
  copiedFrom: string; // wallet address of trader
  onClose: () => void;
}

const MIN_MARGIN_USD = 1;

export default function CopyModal({ position, copiedFrom, onClose }: CopyModalProps) {
  const { copyPosition, isCopying, status } = useCopyTrade();
  const addCopy = useCopyStore((s) => s.addCopy);
  const { isAuthenticated } = useAuth();
  // Live mark from WS market_state, falls back to leader's snapshot mark
  const liveMarket = useMarketStore((s) => s.markets[position.market_id]);
  const mcfg = MARKET_CONFIGS[position.market_id];

  // Pulled before the mcfg early-return so hook order stays stable across renders
  const { data: health } = useQuery({
    queryKey: ['account-health-copy-modal'],
    queryFn: getAccountHealth,
    enabled: isAuthenticated,
    refetchInterval: 10000,
    staleTime: 5000,
  });

  if (!mcfg) {
    return (
      <Modal isOpen={true} onClose={onClose} title="Copy Trade">
        <p className="text-text-secondary text-sm">Unknown market</p>
      </Modal>
    );
  }

  const priceDec = mcfg.priceDecimals;
  const maxLeverage = mcfg.maxLeverage;
  const initialMarginPct = mcfg.initialMarginHdths / 10000; // e.g. 0.10
  const maintenanceMarginPct = 100 / mcfg.maintenanceMarginHdths; // BTC:2000→5%, MON:1000→10%
  const takerFeePct = mcfg.takerFeeBps / 10000; // e.g. 0.05

  const [leverage, setLeverage] = useState(Math.min(Math.max(1, Math.round(position.leverage)), maxLeverage));
  const [sizingMode, setSizingMode] = useState<'fixed' | 'percent'>('fixed');
  // Store the raw input string so the user can clear it freely. The numeric
  // amountUsd is derived from this; empty/invalid -> 0 (which triggers the
  // belowMin guard and disables the Copy button).
  const [amountInput, setAmountInput] = useState('10');
  const amountUsd = (() => {
    const n = parseFloat(amountInput);
    return Number.isFinite(n) && n > 0 ? n : 0;
  })();
  const [copyPct, setCopyPct] = useState(50); // % of leader's position size

  // --- Mark price: live WS feed > leader snapshot ---
  const markPrice = liveMarket?.mark_price && liveMarket.mark_price > 0
    ? liveMarket.mark_price
    : position.mark_price;
  const leaderMark = position.mark_price;

  // Slippage between leader's snapshot mark and current live mark
  const slippagePct = leaderMark > 0 ? Math.abs(markPrice - leaderMark) / leaderMark * 100 : 0;
  // If the live price moved AGAINST the leader's side, copying now is worse
  const slippageDir = markPrice > leaderMark ? 'up' : 'down';
  const slippageHurts =
    (position.side === 'long' && slippageDir === 'up') ||
    (position.side === 'short' && slippageDir === 'down');

  // Calculate amount from percentage of leader's notional (use live mark for parity)
  const leaderNotional = position.size * markPrice;
  const effectiveAmount = sizingMode === 'percent'
    ? Math.round(leaderNotional * copyPct / 100 / leverage * 100) / 100
    : amountUsd;
  const [slPrice, setSlPrice] = useState<number | ''>('');
  const [tpPrice, setTpPrice] = useState<number | ''>('');
  const [slTpError, setSlTpError] = useState('');

  const notional = effectiveAmount * leverage;
  const size = markPrice > 0 ? notional / markPrice : 0;
  const entryFee = notional * takerFeePct;
  const totalCost = effectiveAmount + entryFee;

  // Affordability
  const available = health?.account?.available ?? null;
  const balance = health?.account?.balance ?? null;
  const marginUsed = health?.account?.margin_used ?? null;
  const insufficientFunds = available !== null && totalCost > available;
  const belowMin = effectiveAmount < MIN_MARGIN_USD;

  // Liquidation price (Perpl SDK formula):
  // liq = entry + side * (MMR - deposit) / size
  // LONG (side=+1): liq = entry - (deposit - MMR) / size  (below entry)
  // SHORT (side=-1): liq = entry + (deposit - MMR) / size  (above entry)
  const mmr = notional * maintenanceMarginPct;
  let liqPrice: number;
  if (size > 0) {
    if (position.side === 'long') {
      liqPrice = markPrice - (effectiveAmount - mmr) / size;
    } else {
      liqPrice = markPrice + (effectiveAmount - mmr) / size;
    }
  } else {
    liqPrice = 0;
  }

  // Distance to liquidation
  const liqDistancePct = markPrice > 0
    ? Math.abs(liqPrice - markPrice) / markPrice * 100
    : 0;

  const handleCopy = async () => {
    // Copy v1 safety gate: live copy is disabled. The copy UI must NOT place a real
    // order via useCopyTrade/placeOrder while COPY_LIVE_ENABLED is false. (Manual
    // terminal trading is unaffected — it uses the same hook with source:'manual'.)
    if (!COPY_LIVE_ENABLED) return;
    if (belowMin || size <= 0) return;
    if (insufficientFunds) return;
    setSlTpError('');

    // Validate SL/TP against live mark
    if (slPrice) {
      if (position.side === 'long' && slPrice >= markPrice) { setSlTpError('SL must be below current price for longs'); return; }
      if (position.side === 'short' && slPrice <= markPrice) { setSlTpError('SL must be above current price for shorts'); return; }
    }
    if (tpPrice) {
      if (position.side === 'long' && tpPrice <= markPrice) { setSlTpError('TP must be above current price for longs'); return; }
      if (position.side === 'short' && tpPrice >= markPrice) { setSlTpError('TP must be below current price for shorts'); return; }
    }

    try {
      await copyPosition({
        marketId: position.market_id,
        symbol: position.symbol,
        side: position.side,
        size,
        leverage,
        entryPrice: markPrice,
        source: 'copy_trade',
      });
      addCopy({
        market_id: position.market_id,
        symbol: position.symbol,
        side: position.side,
        leverage,
        amount_usd: effectiveAmount,
        copied_from: copiedFrom,
        timestamp: Date.now(),
      });

      // Track copy position in DB
      try {
        await api.post('/api/copy/positions', {
          leader_wallet: copiedFrom,
          market_id: position.market_id,
          symbol: position.symbol,
          side: position.side,
          entry_price: markPrice,
          size,
          leverage,
          allocation_usd: effectiveAmount,
          source: 'manual',
        });
      } catch {}

      // Place SL/TP as real orders on Perpl (types 5=stop_loss, 6=take_profit)
      const mcfg = MARKET_CONFIGS[position.market_id];
      if (mcfg && (slPrice || tpPrice)) {
        const slTpPromises: Promise<any>[] = [];
        if (slPrice) {
          slTpPromises.push(
            placeSlTpOrder({
              marketId: position.market_id, side: position.side, orderType: 'sl',
              triggerPrice: slPrice, size,
              priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
            }).catch((e) => console.warn('[SL] failed:', e.message))
          );
        }
        if (tpPrice) {
          slTpPromises.push(
            placeSlTpOrder({
              marketId: position.market_id, side: position.side, orderType: 'tp',
              triggerPrice: tpPrice, size,
              priceDecimals: mcfg.priceDecimals, sizeDecimals: mcfg.sizeDecimals,
            }).catch((e) => console.warn('[TP] failed:', e.message))
          );
        }
        await Promise.allSettled(slTpPromises);
      }

      onClose();
    } catch {
      // error shown by useCopyTrade toast
    }
  };

  return (
    <Modal isOpen={true} onClose={onClose} title="Copy Trade">
      <div className="space-y-5">
        {/* Account balance strip */}
        {isAuthenticated && (
          <div className={clsx(
            'p-3 rounded-lg border',
            insufficientFunds ? 'bg-danger/5 border-danger/30' : 'bg-bg-secondary border-text-secondary/10',
          )}>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary font-medium">Your Account</span>
              {health && !health.connected && (
                <span className="text-[10px] text-warning">No Perpl account — deposit at perpl.xyz</span>
              )}
            </div>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <span className="text-text-secondary">Balance</span>
                <div className="text-text-primary font-medium">{balance !== null ? formatUSD(balance) : '—'}</div>
              </div>
              <div>
                <span className="text-text-secondary">Available</span>
                <div className={clsx('font-semibold', insufficientFunds ? 'text-danger' : 'text-success')}>
                  {available !== null ? formatUSD(available) : '—'}
                </div>
              </div>
              <div>
                <span className="text-text-secondary">Margin Used</span>
                <div className="text-text-primary font-medium">{marginUsed !== null ? formatUSD(marginUsed) : '—'}</div>
              </div>
            </div>
          </div>
        )}

        {/* Position being copied */}
        <div className="p-3 bg-bg-secondary rounded-lg">
          <div className="flex items-center gap-3 mb-2">
            <span className="text-lg font-bold text-text-primary">{position.symbol}</span>
            <span
              className={clsx(
                'font-semibold uppercase px-2 py-0.5 rounded text-xs',
                position.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10',
              )}
            >
              {position.side}
            </span>
            {liveMarket?.mark_price > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-success/10 text-success">LIVE</span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <span className="text-text-secondary">Leader Entry</span>
              <div className="text-text-primary font-medium">${formatPrice(position.entry_price, priceDec)}</div>
            </div>
            <div>
              <span className="text-text-secondary">Current Mark</span>
              <div className="text-text-primary font-medium">${formatPrice(markPrice, priceDec)}</div>
              {slippagePct > 0.05 && (
                <div className={clsx('text-[10px]', slippageHurts ? 'text-danger' : 'text-success')}>
                  {slippageHurts ? '↑' : '↓'} {slippagePct.toFixed(2)}% vs leader
                </div>
              )}
            </div>
            <div>
              <span className="text-text-secondary">Trader Leverage</span>
              <div className="text-text-primary font-medium">{position.leverage}x</div>
            </div>
          </div>
        </div>

        {/* Slippage warning */}
        {slippagePct > 1 && (
          <div className={clsx(
            'p-2.5 rounded-md text-[11px]',
            slippageHurts ? 'bg-danger/5 border border-danger/20 text-danger' : 'bg-success/5 border border-success/20 text-success',
          )}>
            Live price has moved <strong>{slippagePct.toFixed(2)}%</strong> {slippageDir} since the leader's snapshot.
            {slippageHurts
              ? ` Copying now means a worse entry than the leader got.`
              : ` Copying now means a better entry than the leader got.`}
          </div>
        )}

        {/* Sizing Mode */}
        <div>
          <div className="flex gap-1 mb-2">
            <button
              onClick={() => setSizingMode('fixed')}
              className={clsx('text-xs px-3 py-1 rounded-md font-medium', sizingMode === 'fixed' ? 'bg-accent text-white' : 'text-text-secondary bg-bg-secondary')}
            >
              Fixed USD
            </button>
            <button
              onClick={() => setSizingMode('percent')}
              className={clsx('text-xs px-3 py-1 rounded-md font-medium', sizingMode === 'percent' ? 'bg-accent text-white' : 'text-text-secondary bg-bg-secondary')}
            >
              % of Leader
            </button>
          </div>
          {sizingMode === 'fixed' ? (
            <div>
              <label className="block text-xs text-text-secondary mb-1">Margin (USD)</label>
              <input
                type="number"
                inputMode="decimal"
                value={amountInput}
                onChange={(e) => setAmountInput(e.target.value)}
                onBlur={() => {
                  // Clamp on blur so the displayed value matches what we'd submit
                  const n = parseFloat(amountInput);
                  if (!Number.isFinite(n) || n <= 0) setAmountInput('');
                  else if (n > 10000) setAmountInput('10000');
                }}
                placeholder="0"
                min={1}
                max={10000}
                step={1}
                className="input-field"
              />
            </div>
          ) : (
            <div>
              <label className="block text-xs text-text-secondary mb-1">
                Copy {copyPct}% of leader's position (${formatUSD(leaderNotional)} notional)
              </label>
              <input
                type="range"
                value={copyPct}
                onChange={(e) => setCopyPct(Number(e.target.value))}
                min={10}
                max={100}
                step={10}
                className="w-full accent-accent"
              />
              <div className="flex justify-between text-[10px] text-text-secondary mt-0.5">
                <span>10%</span>
                <span>Margin: ${formatUSD(effectiveAmount)}</span>
                <span>100%</span>
              </div>
            </div>
          )}
        </div>

        {/* Leverage */}
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <label className="font-medium text-text-primary">Leverage</label>
            <span className="text-accent font-bold">{leverage}x</span>
          </div>
          <input
            type="range"
            value={leverage}
            onChange={(e) => setLeverage(Number(e.target.value))}
            min={1}
            max={maxLeverage}
            step={1}
            className="w-full h-2 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent"
          />
          <div className="flex justify-between text-[10px] text-text-secondary mt-1">
            <span>1x</span>
            <span>{maxLeverage}x (max)</span>
          </div>
        </div>

        {/* Trade summary */}
        <div className="p-3 bg-bg-secondary rounded-lg">
          <div className="text-[10px] text-text-secondary font-medium uppercase tracking-wider mb-3">Order Summary</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-xs">
            <div className="flex justify-between">
              <span className="text-text-secondary">Side</span>
              <span className={clsx('font-bold', position.side === 'long' ? 'text-success' : 'text-danger')}>
                {position.side.toUpperCase()}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Leverage</span>
              <span className="text-text-primary font-bold">{leverage}x</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Margin</span>
              <span className="text-text-primary font-bold">{formatUSD(effectiveAmount)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Notional</span>
              <span className="text-text-primary font-bold">{formatUSD(notional)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Size</span>
              <span className="text-text-primary font-bold">{size.toFixed(mcfg.sizeDecimals)} {position.symbol}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-secondary">Taker Fee ({(takerFeePct * 100).toFixed(1)}%)</span>
              <span className="text-warning font-bold">{formatUSD(entryFee)}</span>
            </div>
            <div className="flex justify-between col-span-2 pt-2 border-t border-text-secondary/10">
              <span className="text-text-secondary">Est. Liquidation</span>
              <span className="text-danger font-bold">
                ${formatPrice(liqPrice, priceDec)}
                <span className="text-text-secondary font-normal ml-1">({liqDistancePct.toFixed(1)}% away)</span>
              </span>
            </div>
            <div className="flex justify-between col-span-2">
              <span className="text-text-secondary">Total Cost</span>
              <span className="text-text-primary font-bold">{formatUSD(totalCost)}</span>
            </div>
          </div>
        </div>

        {/* SL / TP */}
        <div>
          <div className="text-[10px] text-text-secondary font-medium uppercase tracking-wider mb-2">Risk Management</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-danger mb-1">Stop Loss</label>
              <input
                type="number"
                value={slPrice}
                onChange={(e) => setSlPrice(e.target.value ? Number(e.target.value) : '')}
                placeholder={position.side === 'long' ? 'Below entry' : 'Above entry'}
                step={1 / (10 ** priceDec)}
                className="input-field text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-success mb-1">Take Profit</label>
              <input
                type="number"
                value={tpPrice}
                onChange={(e) => setTpPrice(e.target.value ? Number(e.target.value) : '')}
                placeholder={position.side === 'long' ? 'Above entry' : 'Below entry'}
                step={1 / (10 ** priceDec)}
                className="input-field text-sm"
              />
            </div>
          </div>
          {slTpError && <div className="text-[10px] text-danger mt-1">{slTpError}</div>}
        </div>

        {/* Market rules */}
        <div className="text-[10px] text-text-secondary grid grid-cols-3 gap-2">
          <div>Initial Margin: {(initialMarginPct * 100).toFixed(0)}%</div>
          <div>Maint. Margin: {(maintenanceMarginPct * 100).toFixed(0)}%</div>
          <div>Max Impact: {mcfg.maxPriceImpactPct}%</div>
        </div>

        {/* Copy v1: live copy disabled notice */}
        {!COPY_LIVE_ENABLED && (
          <div className="p-3 bg-warning/5 border border-warning/20 rounded-lg">
            <p className="text-xs text-text-secondary">
              <span className="text-warning font-semibold">Live copy disabled (v1):</span>{' '}
              Copy trading is being rebuilt as paper (simulated) copy with a risk engine.
              Real copy orders are turned off — this will not place a position.
            </p>
          </div>
        )}

        {/* Risk warning */}
        {COPY_LIVE_ENABLED && (
          <div className="p-3 bg-danger/5 border border-danger/10 rounded-lg">
            <p className="text-xs text-text-secondary">
              <span className="text-danger font-semibold">Risk:</span>{' '}
              This opens a real position on Perpl. At {leverage}x, liquidation is {liqDistancePct.toFixed(1)}% away.
              Higher leverage = closer liquidation.
            </p>
          </div>
        )}

        {status && (
          <div className="text-xs text-accent animate-pulse">{status}</div>
        )}

        {/* Affordability / min-margin warnings */}
        {belowMin && (
          <div className="text-[11px] text-warning bg-warning/5 border border-warning/20 rounded-md px-2.5 py-2">
            Margin must be at least {formatUSD(MIN_MARGIN_USD)}.
          </div>
        )}
        {insufficientFunds && available !== null && (
          <div className="text-[11px] text-danger bg-danger/5 border border-danger/20 rounded-md px-2.5 py-2">
            Total cost {formatUSD(totalCost)} exceeds available {formatUSD(available)} by {formatUSD(totalCost - available)}.
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button onClick={onClose} disabled={isCopying} className="btn-secondary flex-1">
            Cancel
          </button>
          <button
            onClick={handleCopy}
            disabled={!COPY_LIVE_ENABLED || isCopying || belowMin || insufficientFunds || size <= 0}
            className={clsx(
              'flex-1 font-semibold py-2.5 px-4 rounded transition-colors',
              !COPY_LIVE_ENABLED
                ? 'bg-bg-secondary text-text-secondary border border-text-secondary/20'
                : position.side === 'long'
                  ? 'bg-success text-white hover:bg-success/80'
                  : 'bg-danger text-white hover:bg-danger/80',
              (!COPY_LIVE_ENABLED || isCopying || belowMin || insufficientFunds || size <= 0) && 'opacity-50 cursor-not-allowed',
            )}
          >
            {!COPY_LIVE_ENABLED
              ? 'Live Copy Disabled'
              : isCopying
                ? 'Executing...'
                : insufficientFunds
                  ? 'Insufficient funds'
                  : belowMin
                    ? `Min ${formatUSD(MIN_MARGIN_USD)}`
                    : `Copy ${position.side.toUpperCase()} ${position.symbol} ${leverage}x`}
          </button>
        </div>
      </div>
    </Modal>
  );
}
