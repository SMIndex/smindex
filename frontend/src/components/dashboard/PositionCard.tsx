import { clsx } from 'clsx';
import { MARKETS } from '@/config/constants';
import { formatPrice, formatUSD } from '@/lib/formatters';

interface Position {
  market_id: number;
  side: 'long' | 'short';
  size: number;
  entry_price: number;
  mark_price: number;
  pnl: number;
  leverage: number;
}

interface PositionCardProps {
  position: Position;
}

export default function PositionCard({ position }: PositionCardProps) {
  const marketConfig = MARKETS[position.market_id];
  const symbol = marketConfig?.symbol ?? `MKT-${position.market_id}`;
  const decimals = marketConfig?.decimals ?? 2;
  const isLong = position.side === 'long';
  const isPnlPositive = position.pnl >= 0;

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{symbol}</span>
          <span
            className={clsx(
              'text-xs font-semibold px-2 py-0.5 rounded',
              isLong
                ? 'text-success bg-success/10'
                : 'text-danger bg-danger/10',
            )}
          >
            {isLong ? 'LONG' : 'SHORT'}
          </span>
          <span className="text-xs text-text-secondary bg-bg-secondary px-1.5 py-0.5 rounded">
            {position.leverage}x
          </span>
        </div>
        <span
          className={clsx(
            'text-sm font-bold',
            isPnlPositive ? 'text-success' : 'text-danger',
          )}
        >
          {isPnlPositive ? '+' : ''}
          {formatUSD(position.pnl)}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <div className="text-text-secondary mb-0.5">Size</div>
          <div className="text-text-primary font-medium">
            {position.size.toFixed(4)}
          </div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5">Entry</div>
          <div className="text-text-primary font-medium">
            ${formatPrice(position.entry_price, decimals)}
          </div>
        </div>
        <div>
          <div className="text-text-secondary mb-0.5">Mark</div>
          <div className="text-text-primary font-medium">
            ${formatPrice(position.mark_price, decimals)}
          </div>
        </div>
      </div>
    </div>
  );
}
