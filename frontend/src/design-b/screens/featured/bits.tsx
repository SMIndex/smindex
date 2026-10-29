import type { ReactNode } from 'react';
import { labelWords } from '@/lib/featuredView';

// Small shared pieces of the Featured traders screens (design/README tokens).

export function VenueBadge({ venue }: { venue: 'hl' | 'perpl' }) {
  return <span className={`venue ${venue}`} title={venue === 'hl' ? 'Hyperliquid' : 'Perpl'}>{venue === 'hl' ? 'H' : 'P'}</span>;
}

export function LabelChips({ label, oneBigWin, inactive, history }: { label: string | null; oneBigWin?: boolean; inactive?: boolean; history?: string | null }) {
  return (
    <>
      {label && <span className={`chip ${label === 'consistent' ? 'chip-steady' : 'chip-big'}`}>{labelWords(label)}</span>}
      {oneBigWin && <span className="chip chip-warn" title="Over half of the 90-day profit came from one trade"> One big win</span>}
      {inactive && <span className="chip chip-grey" title="No trade in 14 days; the record stays, nothing to copy right now">Inactive</span>}
      {history && <span className="chip chip-grey" title={`Hyperliquid serves the 10,000 most recent fills; this record starts ${history}`}>History from {history}</span>}
    </>
  );
}

export function Arrow({ dir, title }: { dir: string | null | undefined; title?: string }) {
  if (!dir || dir === 'flat') return null;
  return <span className={dir === 'up' ? 'up' : 'down'} title={title ?? (dir === 'up' ? 'Last 30 days above the 90-day value' : 'Last 30 days below the 90-day value')} aria-label={dir}>{dir === 'up' ? '▲' : '▼'}</span>;
}

export function Pill({ label, value, children, pressed, title }: { label: string; value?: string; children?: ReactNode; pressed?: boolean; title?: string }) {
  return (
    <span className="pill" aria-pressed={pressed} title={title}>
      <b>{label}</b>{value != null && <span className="v">{value} ▾</span>}
      {children}
    </span>
  );
}

export function PillSelect<T extends string | number>({ label, value, opts, fmt, onChange, title }: {
  label: string; value: T; opts: T[]; fmt: (v: T) => string; onChange: (v: T) => void; title?: string;
}) {
  const isNum = typeof value === 'number';
  return (
    <Pill label={label} value={fmt(value)} title={title} pressed={false}>
      <select aria-label={label} value={String(value)} onChange={(e) => onChange((isNum ? Number(e.target.value) : e.target.value) as T)}>
        {opts.map((o) => <option key={String(o)} value={String(o)}>{fmt(o)}</option>)}
      </select>
    </Pill>
  );
}

export function Drawer({ onClose, children, narrow, label }: { onClose: () => void; children: ReactNode; narrow?: boolean; label: string }) {
  return (
    <div className="ft-drawer-wrap" onClick={onClose}>
      <div className={`ft-drawer${narrow ? ' narrow' : ''}`} role="dialog" aria-modal="true" aria-label={label} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}

export function LiveDot({ at, maxSec = 60 }: { at: string | null | undefined; maxSec?: number }) {
  let age: number | null = null;
  if (at) {
    const ms = Date.parse(at.endsWith('Z') || at.includes('+') ? at : at + 'Z');
    if (Number.isFinite(ms)) age = (Date.now() - ms) / 1000;
  }
  const stale = age == null || age > maxSec;
  const txt = age == null ? 'no live read yet' : age < 60 ? `${Math.round(age)} s ago` : age < 3600 ? `${Math.round(age / 60)} m ago` : `${Math.round(age / 3600)} h ago`;
  return <span className="sub"><span className={`live-dot${stale ? ' stale' : ''}`} />{txt}</span>;
}
