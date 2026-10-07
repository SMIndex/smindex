import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import { formatPrice, formatPercent, formatCompact } from '@/lib/formatters';
import type { MarketState } from '@/types/market';

interface MarketTickerProps {
  marketId: number;
  market: MarketState | undefined;
  config: { symbol: string; name: string; decimals: number };
}

export default function MarketTicker({
  marketId,
  market,
  config,
}: MarketTickerProps) {
  const navigate = useNavigate();

  const spread =
    market && market.ask_price && market.bid_price
      ? market.ask_price - market.bid_price
      : null;

  const spreadPct =
    spread !== null && market?.mark_price
      ? (spread / market.mark_price) * 100
      : null;

  const changePositive = market ? market.price_change_24h >= 0 : true;

  return (
    <div
      onClick={() => navigate(`/heatmap/${marketId}`)}
      className="card cursor-pointer hover:border-accent/30 transition-all duration-200 group"
    >
      {/* Header: symbol + 24h change badge */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center">
            <span className="text-xs font-bold text-accent">
              {config.symbol.slice(0, 3)}
            </span>
          </div>
          <div>
            <div className="text-sm font-semibold text-text-primary group-hover:text-accent transition-colors">
              {config.symbol}
            </div>
            <div className="text-[10px] text-text-secondary">{config.name}</div>
          </div>
        </div>
        {market && (
          <span
            className={clsx(
              'text-xs font-semibold px-1.5 py-0.5 rounded',
              changePositive
                ? 'text-success bg-success/10'
                : 'text-danger bg-danger/10',
            )}
          >
            {formatPercent(market.price_change_24h)}
          </span>
        )}
      </div>

      {market ? (
        <div className="space-y-3">
          {/* Mark Price - large */}
          <div
            className={clsx(
              'text-xl font-bold',
              changePositive ? 'text-success' : 'text-danger',
            )}
          >
            ${formatPrice(market.mark_price, config.decimals)}
          </div>

          {/* Bid / Ask spread row */}
          <div className="flex items-center gap-1 text-[10px]">
            <span className="text-success">{formatPrice(market.bid_price, config.decimals)}</span>
            <span className="text-text-secondary">/</span>
            <span className="text-danger">{formatPrice(market.ask_price, config.decimals)}</span>
            {spreadPct !== null && (
              <span className="text-text-secondary ml-auto">
                {spreadPct.toFixed(3)}% spread
              </span>
            )}
          </div>

          {/* Stats grid */}
          <div className="grid grid-cols-3 gap-x-2 gap-y-2 text-[11px]">
            <div>
              <span className="text-text-secondary">24h Vol</span>
              <div className="text-text-primary font-medium">
                {market.daily_volume_usd
                  ? formatCompact(market.daily_volume_usd)
                  : formatCompact(market.daily_volume * market.mark_price)}
              </div>
            </div>
            <div>
              <span className="text-text-secondary">Open Interest</span>
              <div className="text-text-primary font-medium">
                {market.open_interest_usd
                  ? formatCompact(market.open_interest_usd)
                  : formatCompact(market.open_interest * market.mark_price)}
              </div>
            </div>
            <div>
              <span className="text-text-secondary">Funding</span>
              <div
                className={clsx(
                  'font-medium',
                  market.funding_rate != null && market.funding_rate >= 0
                    ? 'text-success'
                    : 'text-danger',
                )}
              >
                {market.funding_rate != null
                  ? formatPercent(market.funding_rate * 100)
                  : '--'}
              </div>
            </div>
          </div>

          {/* OI bar visualization */}
          {market.long_open_interest != null && market.short_open_interest != null && (
            <div>
              <div className="flex justify-between text-[10px] mb-0.5">
                <span className="text-success">
                  L {formatCompact(market.long_open_interest)}
                </span>
                <span className="text-danger">
                  S {formatCompact(market.short_open_interest)}
                </span>
              </div>
              <div className="w-full h-1.5 rounded-full bg-bg-secondary overflow-hidden flex">
                <div
                  className="h-full bg-success rounded-l-full"
                  style={{
                    width: `${(market.long_open_interest / (market.long_open_interest + market.short_open_interest)) * 100}%`,
                  }}
                />
                <div
                  className="h-full bg-danger rounded-r-full"
                  style={{
                    width: `${(market.short_open_interest / (market.long_open_interest + market.short_open_interest)) * 100}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* TVL row if available */}
          {market.tvl != null && (
            <div className="flex items-center justify-between text-[10px] pt-1 border-t border-border/30">
              <span className="text-text-secondary">TVL</span>
              <span className="text-text-primary font-medium">
                {formatCompact(market.tvl)}
              </span>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="h-7 bg-bg-secondary rounded animate-pulse" />
          <div className="h-3 bg-bg-secondary rounded animate-pulse w-3/4" />
          <div className="grid grid-cols-2 gap-2">
            <div className="h-8 bg-bg-secondary rounded animate-pulse" />
            <div className="h-8 bg-bg-secondary rounded animate-pulse" />
          </div>
          <div className="h-1.5 bg-bg-secondary rounded animate-pulse" />
        </div>
      )}
    </div>
  );
}
