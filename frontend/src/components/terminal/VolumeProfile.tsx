import { useQuery } from '@tanstack/react-query';
import { getCandles } from '@/lib/api';
import { MARKET_CONFIGS } from '@/lib/perplTrading';
import { calculateVolumeProfile } from '@/lib/volumeProfile';
import { formatPrice } from '@/lib/formatters';
import { MARKETS } from '@/config/constants';

interface VolumeProfileProps {
  marketId: number;
  resolution: number;
}

export default function VolumeProfile({ marketId, resolution }: VolumeProfileProps) {
  const mcfg = MARKET_CONFIGS[marketId];
  const config = MARKETS[marketId];
  const pd = mcfg ? 10 ** mcfg.priceDecimals : 10;
  const now = Date.now();
  const range = resolution <= 900 ? 3 * 24 * 3600 * 1000 : 7 * 24 * 3600 * 1000;

  const { data } = useQuery({
    queryKey: ['vp-candles', marketId, resolution],
    queryFn: () => getCandles(marketId, resolution, now - range, now),
    refetchInterval: 60000,
  });

  if (!data?.d?.length) return null;

  const candles = data.d.map((c: any) => ({
    open: c.o / pd,
    high: c.h / pd,
    low: c.l / pd,
    close: c.c / pd,
    volume: parseInt(c.v || '0') / 1e6,
  }));

  const profile = calculateVolumeProfile(candles, 30);
  if (profile.length === 0) return null;

  const maxVol = Math.max(...profile.map((p) => p.volume), 0.01);
  const dec = config?.decimals ?? mcfg?.priceDecimals ?? 2;

  return (
    <div className="flex flex-col h-full text-[10px]">
      <div className="px-2 py-1 border-b border-text-secondary/10 text-text-secondary font-medium">
        Vol Profile
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {[...profile].reverse().map((level, i) => {
          const pct = (level.volume / maxVol) * 100;
          const buyPct = level.volume > 0 ? (level.buyVolume / level.volume) * 100 : 50;
          return (
            <div key={i} className="flex items-center gap-1 px-1 py-[1px]">
              <span className="w-[50px] text-right text-text-secondary font-mono">
                {formatPrice(level.price, dec)}
              </span>
              <div className="flex-1 h-[8px] bg-bg-secondary rounded-sm overflow-hidden flex">
                <div className="h-full bg-success/40" style={{ width: `${pct * buyPct / 100}%` }} />
                <div className="h-full bg-danger/40" style={{ width: `${pct * (100 - buyPct) / 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
