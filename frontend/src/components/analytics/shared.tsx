// Shared analytics components (Design Guide Section A) — extracted from the
// live asset page so every analytics surface renders the same grammar.
// Color families (A2, terminal tokens): teal/long = --green, pink/short =
// --red, amber/warning-conviction = --warn-*, purple/neutral-brand =
// --accent, grey/thin = --surface-2/--dim.
import { formatCompact, shortenAddress, formatTimeAgo } from '@/lib/formatters';
import { convictionLabel, Stance } from '@/lib/analyticsCopy';
import type { Conviction } from '@/lib/analyticsApi';

export const PANEL: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)',
};

export function FreshnessLabel({ computedAt, label }: { computedAt: string | null; label: string }) {
  if (!computedAt) return <span className="text-[10px]" style={{ color: 'var(--faint)' }}>no data yet</span>;
  const ts = new Date(computedAt.endsWith('Z') ? computedAt : computedAt + 'Z').getTime();
  return (
    <span className="text-[10px] whitespace-nowrap" style={{ color: 'var(--faint)' }}
          title={`Computed ${new Date(ts).toUTCString()} — ${label}`}>
      as of {formatTimeAgo(ts)} · {label}
    </span>
  );
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span title={text} className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full text-[9px] cursor-help select-none"
          style={{ border: '1px solid var(--border-strong)', color: 'var(--faint)' }}>i</span>
  );
}

export const STANCE_STYLE: Record<Stance, { label: string; bg: string; fg: string }> = {
  net_long: { label: 'NET LONG', bg: 'var(--green-soft)', fg: 'var(--green)' },
  net_short: { label: 'NET SHORT', bg: 'var(--red-soft)', fg: 'var(--red)' },
  balanced: { label: 'BALANCED', bg: 'var(--surface-2)', fg: 'var(--dim)' },
};

export function VerdictHeader({ stance, lowSample, headline, subtext, right }: {
  stance: Stance; lowSample: boolean; headline: string; subtext?: string;
  right?: React.ReactNode;
}) {
  const s = STANCE_STYLE[stance];
  return (
    <div className="rounded-[14px] p-4 mb-4" style={PANEL}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="px-3 py-1 rounded-full text-[12px] font-extrabold tracking-wide"
              style={lowSample
                ? { background: 'var(--surface-2)', color: 'var(--dim)' }  // A5: neutral — never a tinted pill on thin data
                : { background: s.bg, color: s.fg }}
              title="Stance from notional long/short skew; BALANCED inside a ±10% dead zone. Templates in lib/analyticsCopy.ts.">
          {lowSample ? 'LOW SAMPLE' : s.label}
        </span>
        <div className="min-w-0">
          <div className="text-[14.5px] font-bold" style={{ color: 'var(--text)' }}>{headline}</div>
          {subtext && <div className="text-[12px] mt-0.5" style={{ color: 'var(--dim)' }}>{subtext}</div>}
        </div>
        {right && <div className="ml-auto self-start">{right}</div>}
      </div>
    </div>
  );
}

export function SideColumn({ side, rows }: {
  side: 'long' | 'short';
  rows: { label: string; value: string; tip?: string }[];
}) {
  const isLong = side === 'long';
  return (
    <div className="rounded-[10px] p-2.5 flex-1 min-w-0"
         style={{ background: isLong ? 'var(--green-soft)' : 'var(--red-soft)',
                  border: `1px solid ${isLong ? 'rgba(61,220,132,.25)' : 'rgba(255,84,112,.25)'}` }}>
      <div className="text-[10px] font-bold tracking-wider mb-1.5"
           style={{ color: isLong ? 'var(--green)' : 'var(--red)' }}>
        {isLong ? 'LONGS' : 'SHORTS'}
      </div>
      <div className="flex flex-col gap-1">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-2" title={r.tip}>
            <span className="text-[10.5px]" style={{ color: 'var(--faint)' }}>{r.label}</span>
            <span className="rd-mono text-[12px] font-semibold tabular-nums" style={{ color: 'var(--text)' }}>{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function StatRowList({ rows }: {
  rows: { label: string; value: string | number; highlight?: boolean }[];
}) {
  return (
    <div className="flex flex-col gap-1.5 text-[12px]">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between px-2.5 py-1.5 rounded-[8px]"
             style={{ background: 'var(--surface-2)' }}>
          <span style={{ color: 'var(--dim)' }}>{r.label}</span>
          <span className="rd-mono font-semibold tabular-nums"
                style={{ color: r.highlight ? 'var(--green)' : 'var(--text)' }}>{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function LowSampleCard({ title, wallets }: { title: string; wallets: number }) {
  return (
    <div className="rounded-[14px] p-4" style={PANEL}>
      <div className="text-[13px] font-bold mb-1" style={{ color: 'var(--dim)' }}>{title}</div>
      <div className="text-[11.5px]" style={{ color: 'var(--faint)' }}>low sample: {wallets} wallets</div>
    </div>
  );
}

// A3 pill grammar — the ONLY event-pill styling on any analytics surface:
// FLIP amber · OPEN tinted by side · CLOSE teal · INCREASE/REDUCE side-tinted
// small · BIG purple when |Δ| > p95 of trailing 7d.
function eventPillStyle(type: string, side: string): React.CSSProperties {
  if (type === 'FLIP') return { background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--text)', fontWeight: 800 };
  if (type === 'CLOSE') return { background: 'var(--green-soft)', color: 'var(--green)' };
  const long = side === 'long';
  const base = long ? { background: 'var(--green-soft)', color: 'var(--green)' }
                    : { background: 'var(--red-soft)', color: 'var(--red)' };
  if (type === 'INCREASE' || type === 'REDUCE') return { ...base, opacity: 0.85 };
  return base;   // OPEN
}

export interface FlowRowEvent {
  detected_at: string; wallet: string; asset: string; event_type: string;
  side: string; notional_delta: number | null; ref_px: number | null;
  ref_px_approx: boolean; resolution: string; wallet_rank: number | null;
  display_name?: string | null; win_rate_7d?: number | null;
  profit_factor?: number | null;
  conviction?: Conviction | null; is_mm?: boolean; outsized?: boolean;
  merged_count?: number;
}

export function FlowRow({ e }: { e: FlowRowEvent }) {
  const ts = new Date(e.detected_at.endsWith('Z') ? e.detected_at : e.detected_at + 'Z').getTime();
  const small = e.event_type === 'INCREASE' || e.event_type === 'REDUCE';
  return (
    <div className="flex flex-wrap items-center gap-2 px-2.5 py-1.5 rounded-[8px] text-[11.5px]"
         style={{ background: 'var(--surface-2)' }}>
      <span className={`rd-mono font-semibold px-1.5 py-0.5 rounded ${small ? 'text-[9px]' : 'text-[10px]'}`}
            style={eventPillStyle(e.event_type, e.side)}>
        {e.event_type}
      </span>
      {e.outsized && (
        <span className="rd-mono font-bold px-1.5 py-0.5 rounded text-[9.5px]"
              title="Outsized: |notional change| above the 95th percentile of the trailing 7 days."
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          BIG
        </span>
      )}
      <span className="rd-mono" style={{ color: 'var(--text)' }}>{e.asset}</span>
      <span style={{ color: e.side === 'long' ? 'var(--green)' : 'var(--red)' }}>{e.side}</span>
      {(e.merged_count ?? 1) > 1 && (
        <span className="text-[9.5px] px-1 rounded" title="Burst of real-time fills merged into one row (same wallet, asset and side within the alert-merge window). Price is the size-weighted blend, marked ~."
              style={{ border: '1px solid var(--border)', color: 'var(--faint)' }}>
          {e.merged_count} fills
        </span>
      )}
      <span className="rd-mono" style={{ color: 'var(--dim)' }} title={e.wallet}>
        {e.display_name || shortenAddress(e.wallet)}
        {e.wallet_rank != null && <span style={{ color: 'var(--faint)' }}> #{e.wallet_rank}</span>}
      </span>
      {e.win_rate_7d != null && (
        <span className="text-[10px] px-1 rounded"
              title="7d win rate (Tier-2 fill stats) · PF = gross wins ÷ gross losses (∞ = no losses) — win rate never travels without it once computed"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-2)' }}>
          {(e.win_rate_7d * 100).toFixed(0)}% wr{e.profit_factor != null ? ` · PF ${e.profit_factor >= 999 ? '∞' : e.profit_factor.toFixed(1)}` : ''}
        </span>
      )}
      {e.conviction != null && e.conviction.flagged && <ConvictionBadge conviction={e.conviction} isMm={!!e.is_mm} />}
      <span className="ml-auto rd-mono tabular-nums" style={{ color: 'var(--dim)' }}>
        {e.notional_delta != null ? `${e.notional_delta >= 0 ? '+' : '−'}${formatCompact(Math.abs(e.notional_delta))}` : '—'}
      </span>
      <span className="rd-mono tabular-nums text-[10.5px]" style={{ color: 'var(--faint)' }}
            title={e.ref_px_approx ? 'Approximate reference price (sweep resolution does not see the true fill)' : 'Reference price'}>
        {e.ref_px != null ? `${e.ref_px_approx ? '~' : ''}${e.ref_px >= 100 ? e.ref_px.toFixed(1) : e.ref_px.toFixed(4)}` : ''}
      </span>
      <span className="text-[10px] px-1 rounded" title={e.resolution === 'ws' ? 'Real-time fill (tracker websocket)' : '20-minute sweep resolution'}
            style={{ border: '1px solid var(--border)', color: 'var(--faint)' }}>
        {e.resolution === 'ws' ? 'live' : 'sweep'}
      </span>
      <span className="text-[10px] whitespace-nowrap" style={{ color: 'var(--faint)' }}>{formatTimeAgo(ts)}</span>
    </div>
  );
}

// Conviction badge (B3.4, margin-based per owner decision): amber pill, only
// rendered at >= 25% (flagged); the MM variant contextualizes, not alarms.
const CONVICTION_TIP =
  'Conviction = margin committed to this position as a share of account value. ' +
  'Formula: (position notional ÷ leverage) ÷ account value × 100. ' +
  'Account value and leverage from the latest sweep cycle; display capped at 999%.';

export function ConvictionBadge({ conviction, isMm }: { conviction: Conviction; isMm: boolean }) {
  return (
    <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
          title={`${CONVICTION_TIP} This position: ${conviction.pct}%.`}
          style={{ background: 'var(--warn-bg)', border: '1px solid var(--warn-border)', color: 'var(--text)' }}>
      {convictionLabel(conviction.pct, isMm)}
    </span>
  );
}

// ConvictionBar (B4): label + amber-scale bar + label text
export function ConvictionBar({ conviction, isMm }: { conviction: Conviction; isMm: boolean }) {
  const width = Math.min(conviction.pct, 100);
  return (
    <div className="flex items-center gap-2 text-[10.5px]"
         title={CONVICTION_TIP}>
      <span style={{ color: 'var(--faint)' }} className="shrink-0">conviction</span>
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
        <div className="h-full rounded-full"
             style={{ width: `${width}%`,
                      background: conviction.flagged ? 'var(--warn-border)' : 'var(--dim)',
                      opacity: conviction.flagged ? 1 : 0.6 }} />
      </div>
      <span className="rd-mono tabular-nums shrink-0" style={{ color: conviction.flagged ? 'var(--text)' : 'var(--dim)' }}>
        {convictionLabel(conviction.pct, isMm)}
      </span>
    </div>
  );
}
