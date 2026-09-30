import { useId } from 'react';
import { utcDateTime } from '@/lib/time';
import { formatCompact } from '@/lib/formatters';

interface Props {
  points: number[] | undefined;
  times?: number[];   // unix seconds per point — enables hover tooltips
  width?: number;
  height?: number;
  animate?: boolean;
}

// Inline SVG equity sparkline from REAL cumulative-PnL points. Green if the series
// ends >= its start, else red. Soft gradient area fill + dot at the latest point.
// Non-finite points are dropped BEFORE scaling (a single NaN used to break the
// path mid-chart — the "line stops before the right edge" artifact).
export default function Sparkline({ points: rawPoints, times: rawTimes, width = 120, height = 34, animate = true }: Props) {
  const id = useId().replace(/:/g, '');
  const points: number[] = [];
  const times: number[] = [];
  (rawPoints || []).forEach((v, i) => {
    if (Number.isFinite(v)) {
      points.push(v);
      if (rawTimes && Number.isFinite(rawTimes[i])) times.push(rawTimes[i]);
    }
  });
  if (points.length < 2) {
    return (
      <div className="flex items-center" style={{ width, height }}>
        <div className="w-full h-px" style={{ background: 'var(--border-strong)' }} />
      </div>
    );
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const pad = 3;
  const w = width;
  const h = height;
  const stepX = (w - pad * 2) / (points.length - 1);
  const y = (v: number) => h - pad - ((v - min) / span) * (h - pad * 2);
  const coords = points.map((v, i) => [pad + i * stepX, y(v)] as const);
  const line = coords.map(([x, yy], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${yy.toFixed(1)}`).join(' ');
  const area = `${line} L${coords[coords.length - 1][0].toFixed(1)},${h} L${coords[0][0].toFixed(1)},${h} Z`;
  const up = points[points.length - 1] >= points[0];
  const color = up ? 'var(--green)' : 'var(--red)';
  const [lastX, lastY] = coords[coords.length - 1];
  const len = Math.round(w * 1.6);

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ overflow: 'visible' }}>
      <defs>
        <linearGradient id={`sg${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#sg${id})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={animate ? { strokeDasharray: len, strokeDashoffset: len, animation: `rdDraw 1.1s ease forwards` } : undefined}
      />
      <circle cx={lastX} cy={lastY} r="2" fill={color} />
      {times.length === points.length && coords.map(([x, yy], i) => (
        <circle key={i} cx={x} cy={yy} r="7" fill="transparent">
          <title>{`${utcDateTime(times[i])} · ${formatCompact(points[i])}`}</title>
        </circle>
      ))}
    </svg>
  );
}
