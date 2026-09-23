import { useState } from 'react';
import { clsx } from 'clsx';
import { useMarketStore } from '@/stores/marketStore';
import { useTradingStore } from '@/stores/tradingStore';
import { MARKETS } from '@/config/constants';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { calculateEstimatedFee, normalizePerplFeeBps, PERPL_CLOSE_FEE_BPS } from '@/lib/perplFees';
import { formatUSD, formatPrice, formatPercent } from '@/lib/formatters';

export default function PositionCalculator({ marketId }: { marketId: number }) {
  const market = useMarketStore((s) => s.markets[marketId]);
  const mcfg = MARKET_CONFIGS[marketId];
  const config = MARKETS[marketId];

  const [side, setSide] = useState<'long' | 'short'>('long');
  const [entryPrice, setEntryPrice] = useState(market?.mark_price ?? 0);
  const [exitPrice, setExitPrice] = useState(market?.mark_price ?? 0);
  const [margin, setMargin] = useState(100);
  const [leverage, setLeverage] = useState(5);

  if (!mcfg || !config) return null;

  const maxLev = mcfg.maxLeverage;
  const dec = mcfg.priceDecimals;
  const mmrFraction = 100 / mcfg.maintenanceMarginHdths;
  const takerBps = normalizePerplFeeBps(mcfg.takerFeeBps); // guard against unscaled raw (880 -> 8.8)

  // Calculations. Perpl charges the OPEN side only (Taker/Maker Close = 0), so a
  // round-trip pays the taker fee once on entry and 0 on exit — NOT entry+exit.
  const notional = margin * leverage;
  const size = entryPrice > 0 ? notional / entryPrice : 0;
  const entryFee = calculateEstimatedFee(notional, takerBps);
  const exitNotional = size * exitPrice;
  const exitFee = calculateEstimatedFee(exitNotional, PERPL_CLOSE_FEE_BPS); // 0
  const totalFees = entryFee + exitFee;

  const grossPnl = side === 'long'
    ? (exitPrice - entryPrice) * size
    : (entryPrice - exitPrice) * size;
  const netPnl = grossPnl - totalFees;
  const roe = margin > 0 ? (netPnl / margin) * 100 : 0;

  const mmr = notional * mmrFraction;
  const liqPrice = side === 'long'
    ? entryPrice - (margin - mmr) / size
    : entryPrice + (margin - mmr) / size;
  const liqDistPct = entryPrice > 0 ? Math.abs(liqPrice - entryPrice) / entryPrice * 100 : 0;

  // Break-even: where PnL = fees
  const bePrice = side === 'long'
    ? entryPrice + totalFees / size
    : entryPrice - totalFees / size;

  const useMarkPrice = () => {
    if (market) {
      setEntryPrice(market.mark_price);
      setExitPrice(market.mark_price);
    }
  };

  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-text-primary">Position Calculator</span>
        <button onClick={useMarkPrice} className="text-[10px] text-accent hover:underline">Use mark price</button>
      </div>

      {/* Side */}
      <div className="flex gap-1">
        <button onClick={() => setSide('long')} className={clsx('flex-1 py-1.5 rounded text-xs font-semibold', side === 'long' ? 'bg-success text-white' : 'bg-bg-secondary text-text-secondary')}>Long</button>
        <button onClick={() => setSide('short')} className={clsx('flex-1 py-1.5 rounded text-xs font-semibold', side === 'short' ? 'bg-danger text-white' : 'bg-bg-secondary text-text-secondary')}>Short</button>
      </div>

      {/* Inputs */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] text-text-secondary">Entry Price</label>
          <input type="number" value={entryPrice || ''} onChange={(e) => setEntryPrice(Number(e.target.value))} step={1 / 10 ** dec} className="input-field text-xs mt-0.5" />
        </div>
        <div>
          <label className="text-[10px] text-text-secondary">Exit Price</label>
          <input type="number" value={exitPrice || ''} onChange={(e) => setExitPrice(Number(e.target.value))} step={1 / 10 ** dec} className="input-field text-xs mt-0.5" />
        </div>
        <div>
          <label className="text-[10px] text-text-secondary">Margin (USD)</label>
          <input type="number" value={margin} onChange={(e) => setMargin(Number(e.target.value))} min={1} className="input-field text-xs mt-0.5" />
        </div>
        <div>
          <label className="text-[10px] text-text-secondary">Leverage ({leverage}x)</label>
          <input type="range" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} min={1} max={maxLev} className="w-full mt-2 accent-accent" />
        </div>
      </div>

      {/* Results */}
      <div className="p-3 bg-bg-secondary rounded-lg space-y-1.5">
        <div className="flex justify-between">
          <span className="text-text-secondary">Notional</span>
          <span className="text-text-primary">{formatUSD(notional)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Size</span>
          <span className="text-text-primary">{size.toFixed(mcfg.sizeDecimals)} {config.symbol}</span>
        </div>
        <div className="flex justify-between border-t border-text-secondary/10 pt-1.5">
          <span className="text-text-secondary">Gross PnL</span>
          <span className={clsx('font-bold', grossPnl >= 0 ? 'text-success' : 'text-danger')}>
            {grossPnl >= 0 ? '+' : ''}{formatUSD(grossPnl)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Fee (taker open, {takerBps} bps · close 0)</span>
          <span className="text-warning">-{formatUSD(totalFees)}</span>
        </div>
        <div className="flex justify-between border-t border-text-secondary/10 pt-1.5">
          <span className="text-text-secondary font-medium">Net PnL</span>
          <span className={clsx('font-bold', netPnl >= 0 ? 'text-success' : 'text-danger')}>
            {netPnl >= 0 ? '+' : ''}{formatUSD(netPnl)}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">ROE</span>
          <span className={clsx('font-bold', roe >= 0 ? 'text-success' : 'text-danger')}>
            {roe >= 0 ? '+' : ''}{roe.toFixed(2)}%
          </span>
        </div>
        <div className="flex justify-between border-t border-text-secondary/10 pt-1.5">
          <span className="text-text-secondary">Break-even Price</span>
          <span className="text-accent">${formatPrice(Math.max(0, bePrice), dec)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Liquidation Price</span>
          <span className="text-danger">${formatPrice(Math.max(0, liqPrice), dec)} ({liqDistPct.toFixed(1)}%)</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Maint. Margin ({(mmrFraction * 100).toFixed(0)}%)</span>
          <span className="text-text-primary">{formatUSD(mmr)}</span>
        </div>
      </div>
    </div>
  );
}
