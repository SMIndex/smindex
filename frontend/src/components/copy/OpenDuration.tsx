import { useEffect, useState } from 'react';
import { utcDate, utcDateTime, compactDuration } from '@/lib/time';

// "How long has this trade been open" — compact `756d 10h 2m` (no seconds:
// HL open times are day/hour-granular, and seconds are noise everywhere).
// openedAt is unix seconds; null -> em dash.

export function formatOpenedDate(openedAt?: number | null): string {
  // Date ONLY (UTC): the underlying HL value is day-granular — a clock time
  // here would be false precision (HL_QUALITY_REPORT A1/F1).
  return utcDate(openedAt);
}

// Dating confidence (source column): 'fill' EXACT · 'fund_hr' HOUR ·
// 'funding' DAY · 'bound' open-before. Non-EXACT dates carry a ≈ marker and
// a tooltip saying why — a date the data cannot support is never shown bare.
export const DATE_CONFIDENCE_TIP: Record<string, string> = {
  fill: 'Exact — from the venue fill history.',
  fund_hr: 'Hour-resolution — dated from hourly funding payments (≈). A flat-and-reopen inside a single hour is invisible at this resolution.',
  funding: 'Day-resolution — dated from the daily-aggregated funding ledger (≈). An intraday flat-and-reopen is invisible at this resolution.',
  bound: 'Exact open date unresolvable — the position is provably open since this moment; venue history reaches no further.',
};

export function openedDateLabel(openedAt?: number | null, openedBefore?: number | null,
                                source?: string): { text: string; tip?: string } {
  if (openedAt) {
    const approx = source === 'fund_hr' || source === 'funding';
    return { text: `${approx ? '≈ ' : ''}${utcDate(openedAt)}`,
             tip: source ? DATE_CONFIDENCE_TIP[source] : undefined };
  }
  if (openedBefore) {
    return { text: `before ${utcDate(openedBefore)}`, tip: DATE_CONFIDENCE_TIP.bound };
  }
  return { text: '—' };
}

function formatElapsed(openedAt: number): string {
  return compactDuration(Date.now() / 1000 - openedAt);
}

// HL variant: exact timer from fill history, or from the funding ledger (±1h)
// for older positions — resolved in the background, "dating…" while it runs.
export function OpenDurationOrBound({ openedAt, openedBefore, pending, source, compact }: {
  openedAt?: number | null; openedBefore?: number | null;
  pending?: boolean; source?: string; compact?: boolean;
}) {
  if (openedAt) {
    const approx = source === 'fund_hr' || source === 'funding';
    return (
      <span title={source ? DATE_CONFIDENCE_TIP[source] : undefined}>
        {approx && <span className="text-text-secondary/60">≈ </span>}
        <OpenDuration openedAt={openedAt} compact={compact} />
        {source === 'funding' && <span className="text-[9px] text-text-secondary/50"> day-res</span>}
      </span>
    );
  }
  if (pending) {
    return <span className="animate-pulse text-text-secondary/60" title="Resolving open time from the venue's fill/funding history…">dating…</span>;
  }
  if (openedBefore) {
    return (
      <span className="text-text-secondary/50 whitespace-nowrap"
            title={`Provably open since ${utcDate(openedBefore)}; the exact open date is unresolvable from venue history.`}>
        {'>'}{compactDuration(Date.now() / 1000 - openedBefore)}
      </span>
    );
  }
  return <span className="text-text-secondary/40">—</span>;
}

export default function OpenDuration({ openedAt, compact: _compact = false }: { openedAt?: number | null; compact?: boolean }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!openedAt) return;
    const id = setInterval(() => tick((n) => n + 1), 30000);   // minute display
    return () => clearInterval(id);
  }, [openedAt]);

  if (!openedAt) return <span className="text-text-secondary/40">—</span>;
  return (
    <span className="tabular-nums whitespace-nowrap" title={utcDateTime(openedAt)}>
      {formatElapsed(openedAt)}
    </span>
  );
}
