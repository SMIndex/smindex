import { clsx } from 'clsx';
import { useMarketStore } from '@/stores/marketStore';
import { MARKETS } from '@/config/constants';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { formatUSD, formatPrice, formatCompact } from '@/lib/formatters';

interface PositionsListProps {
  positions: any[];
}

export default function PositionsList({ positions }: PositionsListProps) {
  const markets = useMarketStore((s) => s.markets);

  if (positions.length === 0) {
    return <div className="text-center py-8 text-sm text-text-secondary">No open positions</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary border-b border-text-secondary/10">
            <th className="px-3 py-2 text-left font-medium">Market</th>
            <th className="px-2 py-2 text-left font-medium">Size</th>
            <th className="px-2 py-2 text-left font-medium">Entry</th>
            <th className="px-2 py-2 text-left font-medium">Mark</th>
            <th className="px-2 py-2 text-right font-medium">PnL</th>
            <th className="px-2 py-2 text-right font-medium">ROE</th>
            <th className="px-2 py-2 text-right font-medium">Funding</th>
            <th className="px-2 py-2 text-right font-medium">Margin</th>
            <th className="px-2 py-2 text-right font-medium">Notional</th>
            <th className="px-3 py-2 text-right font-medium">Liq Price</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((pos: any) => {
            const mcfg = MARKET_CONFIGS[pos.market_id];
            const cfg = MARKETS[pos.market_id];
            const dec = cfg?.decimals ?? mcfg?.priceDecimals ?? 2;
            const m = markets[pos.market_id];
            const markPrice = m?.mark_price ?? pos.mark_price;

            const livePnl = pos.side === 'long'
              ? (markPrice - pos.entry_price) * pos.size
              : (pos.entry_price - markPrice) * pos.size;
            const roe = pos.deposit > 0 ? (livePnl / pos.deposit) * 100 : 0;

            const mmrFrac = 100 / (mcfg?.maintenanceMarginHdths ?? 2000);
            const mmr = pos.notional * mmrFrac;
            const liqPrice = pos.side === 'long'
              ? pos.entry_price - (pos.deposit - mmr) / pos.size
              : pos.entry_price + (pos.deposit - mmr) / pos.size;

            return (
              <tr key={pos.market_id} className="border-b border-text-secondary/5 hover:bg-bg-secondary/30">
                <td className="px-3 py-2.5">
                  <span className="font-bold text-text-primary">{pos.symbol}</span>
                  <span className={clsx('ml-1 text-[10px] font-semibold uppercase', pos.side === 'long' ? 'text-success' : 'text-danger')}>
                    {pos.side} {pos.leverage}x
                  </span>
                </td>
                <td className="px-2 py-2.5 text-text-primary">{pos.size.toFixed(mcfg?.sizeDecimals ?? 4)}</td>
                <td className="px-2 py-2.5 text-text-primary">${formatPrice(pos.entry_price, dec)}</td>
                <td className="px-2 py-2.5 text-text-primary">${formatPrice(markPrice, dec)}</td>
                <td className="px-2 py-2.5 text-right">
                  <span className={clsx('font-bold', livePnl >= 0 ? 'text-success' : 'text-danger')}>
                    {livePnl >= 0 ? '+' : ''}{formatUSD(livePnl)}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right">
                  <span className={clsx('font-medium', roe >= 0 ? 'text-success' : 'text-danger')}>
                    {roe >= 0 ? '+' : ''}{roe.toFixed(1)}%
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right">
                  <span className={clsx('font-medium', (pos.funding_pnl ?? 0) >= 0 ? 'text-success' : 'text-danger')}>
                    {formatUSD(pos.funding_pnl ?? 0)}
                  </span>
                </td>
                <td className="px-2 py-2.5 text-right text-text-primary">{formatUSD(pos.deposit)}</td>
                <td className="px-2 py-2.5 text-right text-text-primary">{formatCompact(pos.notional)}</td>
                <td className="px-3 py-2.5 text-right text-danger">${formatPrice(Math.max(0, liqPrice), dec)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
