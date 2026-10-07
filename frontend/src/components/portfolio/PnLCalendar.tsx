import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuth } from '@/hooks/useAuth';
import { clsx } from 'clsx';
import api from '@/lib/api';
import { formatUSD } from '@/lib/formatters';

interface EquitySnapshot {
  timestamp: string;
  equity: number;
}

function getDayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function getColor(pnl: number, maxAbs: number): string {
  if (pnl === 0) return 'bg-text-secondary/10';
  const intensity = Math.min(Math.abs(pnl) / (maxAbs || 1), 1);
  if (pnl > 0) {
    if (intensity > 0.7) return 'bg-success';
    if (intensity > 0.4) return 'bg-success/60';
    return 'bg-success/30';
  } else {
    if (intensity > 0.7) return 'bg-danger';
    if (intensity > 0.4) return 'bg-danger/60';
    return 'bg-danger/30';
  }
}

export default function PnLCalendar() {
  const { address } = useAuth();
  const [hoveredDay, setHoveredDay] = useState<{ date: string; pnl: number; x: number; y: number } | null>(null);

  const { data: curveData } = useQuery({
    queryKey: ['equity-curve', address, 90],
    queryFn: () => api.get(`/api/account-health/equity-curve?days=90`).then((r) => r.data as EquitySnapshot[]),
    enabled: !!address,
    refetchInterval: 60000,
  });

  const { dailyPnl, maxAbs, days } = useMemo(() => {
    if (!curveData || curveData.length === 0) return { dailyPnl: {} as Record<string, number>, maxAbs: 0, days: [] as string[] };

    // Group snapshots by day, take last snapshot of each day
    const byDay: Record<string, number> = {};
    for (const snap of curveData) {
      const key = getDayKey(new Date(snap.timestamp));
      byDay[key] = snap.equity;
    }

    // Compute daily PnL as diff between consecutive days
    const sortedDays = Object.keys(byDay).sort();
    const pnl: Record<string, number> = {};
    for (let i = 1; i < sortedDays.length; i++) {
      pnl[sortedDays[i]] = byDay[sortedDays[i]] - byDay[sortedDays[i - 1]];
    }

    const absValues = Object.values(pnl).map(Math.abs);
    const max = absValues.length > 0 ? Math.max(...absValues) : 0;

    // Generate last 90 days
    const allDays: string[] = [];
    const now = new Date();
    for (let i = 89; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      allDays.push(getDayKey(d));
    }

    return { dailyPnl: pnl, maxAbs: max, days: allDays };
  }, [curveData]);

  // Organize into weeks (columns) of 7 rows
  const weeks: string[][] = [];
  let currentWeek: string[] = [];

  // Pad start to align with day of week
  if (days.length > 0) {
    const firstDay = new Date(days[0]);
    const startPad = firstDay.getDay(); // 0=Sun
    for (let i = 0; i < startPad; i++) {
      currentWeek.push('');
    }
  }

  for (const day of days) {
    currentWeek.push(day);
    if (currentWeek.length === 7) {
      weeks.push(currentWeek);
      currentWeek = [];
    }
  }
  if (currentWeek.length > 0) {
    while (currentWeek.length < 7) currentWeek.push('');
    weeks.push(currentWeek);
  }

  const dayLabels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  if (!address) return null;

  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-text-secondary/10 flex items-center justify-between flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-text-primary">PnL Calendar (90 Days)</h2>
        <div className="flex items-center gap-2 sm:gap-3 text-[10px] text-text-secondary">
          <div className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded-sm bg-danger" />
            <span>Loss</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded-sm bg-text-secondary/10" />
            <span>No data</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-2.5 h-2.5 rounded-sm bg-success" />
            <span>Profit</span>
          </div>
        </div>
      </div>
      <div className="p-4 relative overflow-x-auto">
        {days.length === 0 ? (
          <div className="text-center py-8 text-xs text-text-secondary">
            No equity data yet. Snapshots are taken every 4 hours when you have open positions.
          </div>
        ) : (
          <div className="flex gap-0.5">
            {/* Day labels */}
            <div className="flex flex-col gap-0.5 mr-1">
              {dayLabels.map((label, i) => (
                <div key={i} className="h-[14px] flex items-center">
                  {i % 2 === 1 ? (
                    <span className="text-[9px] text-text-secondary w-6">{label}</span>
                  ) : (
                    <span className="w-6" />
                  )}
                </div>
              ))}
            </div>
            {/* Weeks */}
            {weeks.map((week, wi) => (
              <div key={wi} className="flex flex-col gap-0.5">
                {week.map((day, di) => {
                  if (!day) return <div key={di} className="w-[14px] h-[14px]" />;
                  const pnl = dailyPnl[day] ?? 0;
                  const hasData = day in dailyPnl;
                  return (
                    <div
                      key={di}
                      className={clsx(
                        'w-[14px] h-[14px] rounded-sm cursor-pointer transition-all hover:ring-1 hover:ring-text-primary/30',
                        hasData ? getColor(pnl, maxAbs) : 'bg-text-secondary/5',
                      )}
                      onMouseEnter={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        setHoveredDay({ date: day, pnl, x: rect.left, y: rect.top });
                      }}
                      onMouseLeave={() => setHoveredDay(null)}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        )}

        {/* Tooltip */}
        {hoveredDay && (
          <div
            className="fixed z-[100] pointer-events-none bg-bg-card border border-text-secondary/20 rounded-lg shadow-xl px-3 py-2 text-xs"
            style={{
              left: hoveredDay.x + 20,
              top: hoveredDay.y - 10,
            }}
          >
            <div className="text-text-secondary">{new Date(hoveredDay.date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</div>
            <div className={clsx('font-bold', hoveredDay.pnl >= 0 ? 'text-success' : 'text-danger')}>
              {hoveredDay.pnl >= 0 ? '+' : ''}{formatUSD(hoveredDay.pnl)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
