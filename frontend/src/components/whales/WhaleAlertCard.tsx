import { clsx } from 'clsx';
import { MARKETS } from '@/config/constants';
import { formatUSD, formatTimeAgo, severityColor } from '@/lib/formatters';
import type { WhaleAlert } from '@/types/whale';

interface WhaleAlertCardProps {
  alert: WhaleAlert;
}

const alertTypeIcons: Record<string, string> = {
  large_order:
    'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
  large_fill:
    'M13 10V3L4 14h7v7l9-11h-7z',
  oi_divergence:
    'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
};

const alertTypeLabels: Record<string, string> = {
  large_order: 'Large Order',
  large_fill: 'Large Fill',
  oi_divergence: 'OI Divergence',
};

export default function WhaleAlertCard({ alert }: WhaleAlertCardProps) {
  const marketConfig = MARKETS[alert.market_id];
  const symbol = marketConfig?.symbol ?? `ID:${alert.market_id}`;

  return (
    <div className="flex items-start gap-3 p-3 bg-bg-secondary rounded-lg border border-transparent hover:border-text-secondary/10 transition-all animate-[fadeIn_0.3s_ease-out]">
      <div className="shrink-0 w-8 h-8 rounded-lg bg-bg-card flex items-center justify-center">
        <svg
          className="w-4 h-4 text-text-secondary"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d={alertTypeIcons[alert.alert_type] || alertTypeIcons.large_order}
          />
        </svg>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-bold text-text-primary">{symbol}</span>
          {alert.side && (
            <span
              className={clsx(
                'text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded',
                alert.side === 'buy' || alert.side === 'long'
                  ? 'text-success bg-success/10'
                  : 'text-danger bg-danger/10',
              )}
            >
              {alert.side}
            </span>
          )}
          <span
            className={clsx(
              'text-[10px] font-medium px-1.5 py-0.5 rounded border',
              severityColor(alert.severity),
            )}
          >
            {alert.severity}
          </span>
        </div>

        <div className="flex items-center gap-2 text-xs flex-wrap">
          <span className="text-text-secondary">
            {alertTypeLabels[alert.alert_type] ?? alert.alert_type}
          </span>
          <span className="text-text-primary font-medium">
            {formatUSD(alert.size_usd)}
          </span>
          {alert.price && (
            <>
              <span className="text-text-secondary">@</span>
              <span className="text-text-primary">
                ${alert.price.toLocaleString()}
              </span>
            </>
          )}
        </div>
      </div>

      <span className="text-[10px] text-text-secondary whitespace-nowrap shrink-0">
        {formatTimeAgo(alert.timestamp)}
      </span>
    </div>
  );
}
