import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import api from '@/lib/api';

export default function OrderFlowBar({ marketId }: { marketId: number }) {
  const { data } = useQuery({
    queryKey: ['order-flow', marketId],
    queryFn: () => api.get(`/api/order-flow/${marketId}`).then((r) => r.data),
    refetchInterval: 10000,
  });

  if (!data || data.length === 0) return null;

  const latest = data[data.length - 1];
  const total = latest.bid_vol + latest.ask_vol;
  const bidPct = total > 0 ? (latest.bid_vol / total) * 100 : 50;
  const pressure = latest.delta > 0 ? 'Buying' : latest.delta < 0 ? 'Selling' : 'Neutral';

  return (
    <div className="flex items-center gap-2 px-3 py-1.5 border-t border-text-secondary/10 text-[10px]">
      <span className="text-text-secondary">Flow:</span>
      <div className="flex-1 h-2 bg-bg-secondary rounded-full overflow-hidden flex">
        <div className="h-full bg-success/60 rounded-l-full" style={{ width: `${bidPct}%` }} />
        <div className="h-full bg-danger/60 rounded-r-full" style={{ width: `${100 - bidPct}%` }} />
      </div>
      <span className={clsx('font-medium', latest.delta > 0 ? 'text-success' : latest.delta < 0 ? 'text-danger' : 'text-text-secondary')}>
        {pressure} {Math.abs(bidPct - 50).toFixed(0)}%
      </span>
    </div>
  );
}
