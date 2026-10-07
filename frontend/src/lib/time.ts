// Shared time formatting (HL_QUALITY_REPORT Part F).
// ALL user-facing timestamps render in UTC with an explicit suffix — never the
// browser locale/zone implicitly. Dates without a clock use utcDate().

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** `17 Jul 2025` — date only, UTC. Input unix seconds. */
export function utcDate(unixSec?: number | null): string {
  if (!unixSec) return '—';
  const d = new Date(unixSec * 1000);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** `17 Jul 2025, 14:05 UTC` — full stamp. Input unix seconds. */
export function utcDateTime(unixSec?: number | null): string {
  if (!unixSec) return '—';
  const d = new Date(unixSec * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC`;
}

/** `4m` / `3h` / `2d` — compact relative age vs now. Input unix seconds (UTC). */
export function relativeAge(unixSec?: number | null): string {
  if (!unixSec) return '—';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - unixSec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

/** `756d 10h 2m` / `5h 12m` / `12m` — compact duration, no seconds. */
export function compactDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const dd = Math.floor(s / 86400);
  const hh = Math.floor((s % 86400) / 3600);
  const mm = Math.floor((s % 3600) / 60);
  if (dd > 0) return `${dd}d ${hh}h ${mm}m`;
  if (hh > 0) return `${hh}h ${mm}m`;
  return `${mm}m`;
}
