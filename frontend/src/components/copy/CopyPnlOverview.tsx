import { useMemo } from 'react';
import type { CumulativePoint } from '@/lib/copyPortfolio';
import { formatUSD } from '@/lib/formatters';

interface Props {
  points: CumulativePoint[];
}

// Cumulative REALIZED PnL from closed positions only (real data). No synthetic curve.
export default function CopyPnlOverview({ points }: Props) {
  const path = useMemo(() => {
    if (points.length < 2) return null;
    const vals = points.map((p) => p.value);
    const min = Math.min(0, ...vals);
    const max = Math.max(0, ...vals);
    const range = max - min || 1;
    const W = 100, H = 36;
    const step = W / (points.length - 1);
    const y = (v: number) => H - ((v - min) / range) * H;
    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
    const area = `${line} L${W},${H} L0,${H} Z`;
    const last = vals[vals.length - 1];
    return { line, area, zeroY: y(0), up: last >= 0 };
  }, [points]);

  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Realized PnL (cumulative)</h3>
        <span className="text-[10px] text-text-secondary/50">closed copied trades</span>
      </div>
      {!path ? (
        <div className="h-[120px] flex flex-col items-center justify-center text-center gap-1">
          <span className="text-xs text-text-secondary/60">Not enough closed trades to chart yet.</span>
          <span className="text-[10px] text-text-secondary/40">Closed copied positions will build this curve.</span>
        </div>
      ) : (
        <>
          <svg viewBox="0 0 100 36" preserveAspectRatio="none" className="w-full h-[120px]">
            <defs>
              <linearGradient id="copyPnlFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={path.up ? 'var(--green)' : 'var(--red)'} stopOpacity="0.25" />
                <stop offset="100%" stopColor={path.up ? 'var(--green)' : 'var(--red)'} stopOpacity="0" />
              </linearGradient>
            </defs>
            <line x1="0" y1={path.zeroY} x2="100" y2={path.zeroY} stroke="var(--border-strong)" strokeWidth="0.3" strokeDasharray="1,1" />
            <path d={path.area} fill="url(#copyPnlFill)" />
            <path d={path.line} fill="none" stroke={path.up ? 'var(--green)' : 'var(--red)'} strokeWidth="0.8" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="flex justify-between mt-2 text-[10px] text-text-secondary/50">
            <span>{points.length} closed</span>
            <span className={points[points.length - 1].value >= 0 ? 'text-success' : 'text-danger'}>
              {points[points.length - 1].value >= 0 ? '+' : ''}{formatUSD(points[points.length - 1].value)} total
            </span>
          </div>
        </>
      )}
    </div>
  );
}
