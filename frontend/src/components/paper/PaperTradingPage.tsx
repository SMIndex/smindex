import { useState, useEffect, useReducer } from 'react';
import { clsx } from 'clsx';
import { useMarketData } from '@/hooks/useMarketData';
import { usePaperStore, type PaperPosition } from '@/stores/paperStore';
import { MARKETS, MARKET_IDS } from '@/config/constants';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { formatUSD, formatPrice, formatPercent } from '@/lib/formatters';
import PriceChart from '@/components/copytrade/PriceChart';
import LoadingSpinner from '@/components/common/LoadingSpinner';

function OrderForm({ marketId }: { marketId: number }) {
  const { markets } = useMarketData();
  const openPosition = usePaperStore((s) => s.openPosition);
  const balance = usePaperStore((s) => s.balance);
  const mcfg = MARKET_CONFIGS[marketId];
  const market = markets[marketId];
  const config = MARKETS[marketId];

  const [side, setSide] = useState<'long' | 'short'>('long');
  const [amount, setAmount] = useState(100);
  const [leverage, setLeverage] = useState(5);
  const [error, setError] = useState('');

  if (!market || !mcfg || !config) return null;

  const maxLev = mcfg.maxLeverage;
  const bidPrice = market.bid_price;
  const askPrice = market.ask_price;
  const markPrice = market.mark_price;
  const fillPrice = side === 'long' ? askPrice : bidPrice;
  const spread = askPrice - bidPrice;
  const spreadPct = markPrice > 0 ? (spread / markPrice) * 100 : 0;

  const notional = amount * leverage;
  const size = fillPrice > 0 ? notional / fillPrice : 0;
  const takerFee = notional * (mcfg.takerFeeBps / 10000);
  const totalCost = amount + takerFee;

  const mmrFraction = 100 / mcfg.maintenanceMarginHdths;
  const mmr = notional * mmrFraction;

  let liqPrice: number;
  if (side === 'long') {
    liqPrice = fillPrice - (amount - mmr) / size;
  } else {
    liqPrice = fillPrice + (amount - mmr) / size;
  }
  const liqDist = markPrice > 0 ? Math.abs(liqPrice - markPrice) / markPrice * 100 : 0;

  const handleSubmit = () => {
    setError('');
    const err = openPosition({
      market_id: marketId,
      symbol: config.symbol,
      side,
      amount_usd: amount,
      leverage,
      bid_price: bidPrice,
      ask_price: askPrice,
      mark_price: markPrice,
    });
    if (err) setError(err);
  };

  return (
    <div className="card space-y-4">
      {/* Market price header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="text-lg font-bold text-text-primary">{config.symbol}</span>
          <span className="text-lg font-bold text-text-primary">${formatPrice(markPrice, config.decimals)}</span>
          {market.price_change_24h != null && (
            <span className={clsx('text-xs font-medium', market.price_change_24h >= 0 ? 'text-success' : 'text-danger')}>
              {formatPercent(market.price_change_24h)}
            </span>
          )}
        </div>
        <div className="flex gap-3 text-[10px]">
          <span className="text-success">Bid: ${formatPrice(bidPrice, config.decimals)}</span>
          <span className="text-danger">Ask: ${formatPrice(askPrice, config.decimals)}</span>
          <span className="text-text-secondary">Spread: {spreadPct.toFixed(3)}%</span>
        </div>
      </div>

      {/* Side */}
      <div className="flex gap-2">
        <button onClick={() => setSide('long')} className={clsx('flex-1 py-2.5 rounded font-semibold text-sm transition-colors', side === 'long' ? 'bg-success text-white' : 'bg-bg-secondary text-text-secondary hover:text-success')}>Long</button>
        <button onClick={() => setSide('short')} className={clsx('flex-1 py-2.5 rounded font-semibold text-sm transition-colors', side === 'short' ? 'bg-danger text-white' : 'bg-bg-secondary text-text-secondary hover:text-danger')}>Short</button>
      </div>

      {/* Amount */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-text-secondary">Margin (USD)</span>
          <span className="text-text-secondary">Avail: {formatUSD(balance)}</span>
        </div>
        <input type="number" value={amount} onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))} min={1} step={10} className="input-field" />
        <div className="flex gap-1 mt-1">
          {[10, 25, 50, 100, 250, 500].map((v) => (
            <button key={v} onClick={() => setAmount(v)} className="text-[10px] px-2 py-0.5 bg-bg-secondary rounded text-text-secondary hover:text-text-primary">${v}</button>
          ))}
        </div>
      </div>

      {/* Leverage */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="text-text-secondary">Leverage</span>
          <span className="text-accent font-bold">{leverage}x</span>
        </div>
        <input type="range" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} min={1} max={maxLev} step={1} className="w-full h-2 bg-bg-secondary rounded-lg appearance-none cursor-pointer accent-accent" />
        <div className="flex justify-between text-[10px] text-text-secondary mt-0.5">
          <span>1x</span>
          <span>{maxLev}x (max)</span>
        </div>
      </div>

      {/* Order summary */}
      <div className="p-3 bg-bg-secondary rounded-lg text-xs space-y-1.5">
        <div className="flex justify-between">
          <span className="text-text-secondary">Fill Price</span>
          <span className="text-text-primary font-medium">${formatPrice(fillPrice, config.decimals)} ({side === 'long' ? 'ask' : 'bid'})</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Size</span>
          <span className="text-text-primary font-medium">{size.toFixed(mcfg.sizeDecimals)} {config.symbol}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Notional</span>
          <span className="text-text-primary font-medium">{formatUSD(notional)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Taker Fee ({(mcfg.takerFeeBps / 100).toFixed(1)}%)</span>
          <span className="text-warning font-medium">{formatUSD(takerFee)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Total Cost</span>
          <span className="text-text-primary font-bold">{formatUSD(totalCost)}</span>
        </div>
        <div className="flex justify-between pt-1.5 border-t border-text-secondary/10">
          <span className="text-text-secondary">Maint. Margin ({(mmrFraction * 100).toFixed(0)}%)</span>
          <span className="text-text-primary">{formatUSD(mmr)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-text-secondary">Liq. Price</span>
          <span className="text-danger font-bold">
            ${formatPrice(Math.max(0, liqPrice), config.decimals)}
            <span className="text-text-secondary font-normal ml-1">({liqDist.toFixed(1)}% away)</span>
          </span>
        </div>
        {market.funding_rate != null && (
          <div className="flex justify-between">
            <span className="text-text-secondary">Funding Rate (1h)</span>
            <span className={clsx('font-medium', market.funding_rate >= 0 ? 'text-danger' : 'text-success')}>
              {(market.funding_rate * 100).toFixed(4)}% {market.funding_rate >= 0 ? '(longs pay)' : '(shorts pay)'}
            </span>
          </div>
        )}
      </div>

      {error && <div className="text-xs text-danger bg-danger/10 p-2 rounded">{error}</div>}

      <button
        onClick={handleSubmit}
        disabled={amount <= 0 || totalCost > balance}
        className={clsx(
          'w-full py-3 rounded font-semibold text-sm transition-colors',
          side === 'long' ? 'bg-success text-white hover:bg-success/80' : 'bg-danger text-white hover:bg-danger/80',
          (amount <= 0 || totalCost > balance) && 'opacity-50 cursor-not-allowed',
        )}
      >
        {side === 'long' ? 'Long' : 'Short'} {config.symbol} {leverage}x
      </button>
    </div>
  );
}

function PositionRow({ position }: { position: PaperPosition }) {
  const { markets } = useMarketData();
  const closePosition = usePaperStore((s) => s.closePosition);
  const calcPnl = usePaperStore((s) => s.calcDeltaPnl);
  const calcLiq = usePaperStore((s) => s.calcLiqPrice);
  const market = markets[position.market_id];
  const config = MARKETS[position.market_id];
  const mcfg = MARKET_CONFIGS[position.market_id];

  if (!market || !config || !mcfg) return null;

  const markPrice = market.mark_price;
  const deltaPnl = calcPnl(position, markPrice);
  const totalPnl = deltaPnl + position.premium_pnl;
  const roe = position.deposit > 0 ? (totalPnl / position.deposit) * 100 : 0;
  const liqPrice = calcLiq(position);

  return (
    <div className="flex items-center gap-3 p-3 hover:bg-bg-secondary/30 transition-colors text-xs">
      <div className="min-w-[90px] flex items-center gap-1.5">
        <span className="font-bold text-text-primary">{position.symbol}</span>
        <span className={clsx('font-semibold uppercase px-1 py-0.5 rounded text-[10px]', position.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
          {position.side} {position.leverage}x
        </span>
      </div>
      <div className="min-w-[55px]">
        <div className="text-text-secondary">Size</div>
        <div className="text-text-primary">{position.size.toFixed(mcfg.sizeDecimals)}</div>
      </div>
      <div className="min-w-[75px]">
        <div className="text-text-secondary">Entry ({position.side === 'long' ? 'ask' : 'bid'})</div>
        <div className="text-text-primary">${formatPrice(position.entry_price, config.decimals)}</div>
      </div>
      <div className="min-w-[75px]">
        <div className="text-text-secondary">Mark</div>
        <div className="text-text-primary">${formatPrice(markPrice, config.decimals)}</div>
      </div>
      <div className="min-w-[75px]">
        <div className="text-text-secondary">PnL</div>
        <div className={clsx('font-bold', totalPnl >= 0 ? 'text-success' : 'text-danger')}>
          {totalPnl >= 0 ? '+' : ''}{formatUSD(totalPnl)}
          <div className="font-normal text-[10px]">{roe >= 0 ? '+' : ''}{roe.toFixed(2)}% ROE</div>
        </div>
      </div>
      {position.premium_pnl !== 0 && (
        <div className="min-w-[55px]">
          <div className="text-text-secondary">Funding</div>
          <div className={clsx('font-medium', position.premium_pnl >= 0 ? 'text-success' : 'text-danger')}>
            {position.premium_pnl >= 0 ? '+' : ''}{formatUSD(position.premium_pnl)}
          </div>
        </div>
      )}
      <div className="min-w-[75px]">
        <div className="text-text-secondary">Liq.</div>
        <div className="text-danger">${formatPrice(Math.max(0, liqPrice), config.decimals)}</div>
      </div>
      <button
        onClick={() => closePosition(position.id, market.bid_price, market.ask_price)}
        className="ml-auto px-3 py-1.5 rounded text-xs font-semibold bg-danger text-white hover:bg-danger/80"
      >
        Close
      </button>
    </div>
  );
}

export default function PaperTradingPage() {
  const { markets, isLoading } = useMarketData();
  const { balance, starting_balance, positions, history, resetAccount, applyFunding, checkLiquidations, calcDeltaPnl } = usePaperStore();
  const [selectedMarket, setSelectedMarket] = useState(1);

  const config = MARKETS[selectedMarket];

  // Force re-render every second for live PnL ticking
  const [, tick] = useReducer((x: number) => x + 1, 0);
  useEffect(() => {
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);

  // Apply funding and check liquidations on every market update
  useEffect(() => {
    for (const pos of positions) {
      const market = markets[pos.market_id];
      if (!market) continue;
      // Check liquidation
      checkLiquidations(pos.market_id, market.mark_price);
      // Apply funding (will skip if < 1h since last)
      if (market.funding_rate != null) {
        applyFunding(pos.market_id, market.funding_rate);
      }
    }
  }, [markets, positions, checkLiquidations, applyFunding]);

  // Unrealized PnL
  const unrealizedPnl = positions.reduce((sum, pos) => {
    const market = markets[pos.market_id];
    if (!market) return sum;
    return sum + calcDeltaPnl(pos, market.mark_price) + pos.premium_pnl;
  }, 0);

  const marginInUse = positions.reduce((s, p) => s + p.deposit, 0);
  const totalEquity = balance + marginInUse + unrealizedPnl;
  const totalPnl = totalEquity - starting_balance;
  const realizedPnl = history.reduce((s, t) => s + t.total_pnl, 0);
  const wins = history.filter((t) => t.total_pnl > 0).length;
  const losses = history.filter((t) => t.total_pnl <= 0).length;
  const winRate = history.length > 0 ? (wins / history.length) * 100 : 0;
  const totalFees = history.reduce((s, t) => s + t.entry_fee + t.exit_fee, 0) +
    positions.reduce((s, p) => s + p.fee_paid, 0);

  if (isLoading) return <div className="flex justify-center py-16"><LoadingSpinner size="lg" /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Paper Trading</h1>
          <p className="text-sm text-text-secondary">Real Perpl prices, bid/ask fills, funding rates — zero risk</p>
        </div>
        <button onClick={() => { if (confirm('Reset to $10,000?')) resetAccount(); }} className="btn-secondary text-xs">Reset</button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-6 gap-3">
        {[
          { label: 'Equity', value: formatUSD(totalEquity), color: '' },
          { label: 'Available', value: formatUSD(balance), color: '' },
          { label: 'Total PnL', value: `${totalPnl >= 0 ? '+' : ''}${formatUSD(totalPnl)}`, color: totalPnl >= 0 ? 'text-success' : 'text-danger' },
          { label: 'Unrealized', value: `${unrealizedPnl >= 0 ? '+' : ''}${formatUSD(unrealizedPnl)}`, color: unrealizedPnl >= 0 ? 'text-success' : 'text-danger' },
          { label: 'Win Rate', value: `${winRate.toFixed(0)}% (${wins}W/${losses}L)`, color: '' },
          { label: 'Fees Paid', value: formatUSD(totalFees), color: 'text-warning' },
        ].map((s) => (
          <div key={s.label} className="card py-3">
            <div className="text-[10px] uppercase tracking-wider text-text-secondary mb-1">{s.label}</div>
            <div className={clsx('text-lg font-bold', s.color || 'text-text-primary')}>{s.value}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          {/* Market tabs */}
          <div className="flex items-center gap-1 bg-bg-secondary rounded-lg p-1">
            {MARKET_IDS.map((id) => (
              <button key={id} onClick={() => setSelectedMarket(id)}
                className={clsx('px-4 py-2 rounded-md text-sm font-medium transition-colors', selectedMarket === id ? 'bg-accent text-white' : 'text-text-secondary hover:text-text-primary hover:bg-bg-card')}>
                {MARKETS[id].symbol}
                {markets[id] && <span className="ml-1 text-xs opacity-70">${formatPrice(markets[id].mark_price, MARKETS[id].decimals)}</span>}
              </button>
            ))}
          </div>

          {/* Chart */}
          <div className="card p-0 overflow-hidden">
            <PriceChart marketId={selectedMarket} symbol={config?.symbol ?? ''} />
          </div>

          {/* Open Positions */}
          <div className="card p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-text-primary">Open Positions ({positions.length})</h2>
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-success animate-pulse" />
                <span className="text-[10px] text-text-secondary">Live PnL from Perpl</span>
              </div>
            </div>
            {positions.length > 0 ? (
              <div className="divide-y divide-text-secondary/5">
                {positions.map((pos) => <PositionRow key={pos.id} position={pos} />)}
              </div>
            ) : (
              <div className="text-sm text-text-secondary text-center py-8">No open positions</div>
            )}
          </div>

          {/* History */}
          {history.length > 0 && (
            <div className="card p-0 overflow-hidden">
              <div className="px-4 py-3 border-b border-text-secondary/10">
                <h2 className="text-sm font-semibold text-text-primary">Trade History ({history.length})</h2>
              </div>
              <div className="max-h-[300px] overflow-y-auto divide-y divide-text-secondary/5">
                {history.map((t) => {
                  const pnl = isFinite(t.total_pnl) ? t.total_pnl : 0;
                  const roe = isFinite(t.roe) ? t.roe : 0;
                  const fees = (isFinite(t.entry_fee) ? t.entry_fee : 0) + (isFinite(t.exit_fee) ? t.exit_fee : 0);
                  const dec = MARKETS[t.market_id]?.decimals ?? 2;
                  return (
                    <div key={t.id} className="flex items-center gap-3 px-4 py-2.5 text-xs">
                      <span className="font-bold text-text-primary w-[36px]">{t.symbol}</span>
                      <span className={clsx('font-semibold uppercase px-1 py-0.5 rounded text-[10px] w-[55px] text-center', t.side === 'long' ? 'text-success bg-success/10' : 'text-danger bg-danger/10')}>
                        {t.side} {t.leverage}x
                      </span>
                      <span className="text-text-secondary w-[65px]">${formatPrice(t.entry_price || 0, dec)}</span>
                      <span className="text-text-primary">→</span>
                      <span className="text-text-secondary w-[65px]">${formatPrice(t.exit_price || 0, dec)}</span>
                      <span className={clsx('font-bold w-[70px]', pnl >= 0 ? 'text-success' : 'text-danger')}>
                        {pnl >= 0 ? '+' : ''}{formatUSD(pnl)}
                      </span>
                      <span className={clsx('text-[10px] w-[55px]', roe >= 0 ? 'text-success' : 'text-danger')}>
                        {roe >= 0 ? '+' : ''}{roe.toFixed(1)}%
                      </span>
                      <span className="text-warning text-[10px] w-[50px]">-{formatUSD(fees)}</span>
                      <span className="text-text-secondary/50 text-[10px] ml-auto">
                        {roe === -100 ? 'LIQUIDATED' : new Date(t.closed_at).toLocaleTimeString()}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        <div>
          <OrderForm marketId={selectedMarket} />
        </div>
      </div>
    </div>
  );
}
