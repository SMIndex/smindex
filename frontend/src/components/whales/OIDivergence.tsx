import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getOIDivergence } from '@/lib/api';
import { formatPercent } from '@/lib/formatters';
import type { OIDivergence as OIDivergenceType } from '@/types/whale';

interface OIDivergenceProps {
  marketId: number;
}

export default function OIDivergenceDisplay({ marketId }: OIDivergenceProps) {
  const { data } = useQuery<OIDivergenceType>({
    queryKey: ['oi-divergence', marketId],
    queryFn: () => getOIDivergence(marketId),
    refetchInterval: 30_000,
  });

  if (!data) return null;

  const isBullish = data.direction === 'bullish_divergence';

  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-text-primary mb-3">
        OI Divergence
      </h3>

      <div className="space-y-3">
        <div className="flex items-center justify-between text-xs">
          <span className="text-text-secondary">Direction</span>
          <span
            className={clsx(
              'font-semibold px-2 py-0.5 rounded',
              isBullish
                ? 'text-success bg-success/10'
                : 'text-danger bg-danger/10',
            )}
          >
            {isBullish ? 'Bullish Divergence' : 'Bearish Divergence'}
          </span>
        </div>

        <div className="space-y-2">
          {/* OI Change bar */}
          <div>
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="text-text-secondary">OI Change</span>
              <span className="text-text-primary font-medium">
                {formatPercent(data.oi_change_pct)}
              </span>
            </div>
            <div className="h-2 bg-bg-secondary rounded-full overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all',
                  data.oi_change_pct >= 0 ? 'bg-success' : 'bg-danger',
                )}
                style={{
                  width: `${Math.min(Math.abs(data.oi_change_pct) * 5, 100)}%`,
                  marginLeft:
                    data.oi_change_pct < 0
                      ? `${100 - Math.min(Math.abs(data.oi_change_pct) * 5, 100)}%`
                      : undefined,
                }}
              />
            </div>
          </div>

          {/* Price Change bar */}
          <div>
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="text-text-secondary">Price Change</span>
              <span className="text-text-primary font-medium">
                {formatPercent(data.price_change_pct)}
              </span>
            </div>
            <div className="h-2 bg-bg-secondary rounded-full overflow-hidden">
              <div
                className={clsx(
                  'h-full rounded-full transition-all',
                  data.price_change_pct >= 0 ? 'bg-accent' : 'bg-warning',
                )}
                style={{
                  width: `${Math.min(Math.abs(data.price_change_pct) * 5, 100)}%`,
                  marginLeft:
                    data.price_change_pct < 0
                      ? `${100 - Math.min(Math.abs(data.price_change_pct) * 5, 100)}%`
                      : undefined,
                }}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
