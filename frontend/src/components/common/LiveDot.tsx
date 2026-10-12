import { useEffect, useState } from 'react';

import { accountAgeMs, type AccountState } from '@/lib/accountStore';

/**
 * "live · 2s" instead of a spinner (2026-10-09).
 *
 * The owner's complaint was that the app "keeps loading every few seconds":
 * every background refresh used to announce itself with a spinner or a
 * skeleton, which reads as the page reloading. Data that has loaded once stays
 * on screen and is updated in place; the only indicator is this dot, which
 * says how old the newest feed is. Nothing here changes layout: the width is
 * fixed by tabular digits, so a tick cannot shift the row.
 */
export default function LiveDot({ account, className, label = 'live' }: {
  account: AccountState;
  className?: string;
  label?: string;
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, []);

  const age = accountAgeMs(account);
  const never = age === 0;
  const stale = age > 60_000;
  const failing = !!account.error;
  const color = failing ? 'var(--danger, #ef4444)'
    : never ? 'var(--faint, #9aa)'
      : stale ? 'var(--warning, #f59e0b)'
        : 'var(--success, #22c55e)';
  const text = failing ? 'read failed'
    : never ? 'connecting'
      : age < 1500 ? label
        : `${label} · ${age < 60_000 ? `${Math.round(age / 1000)}s` : `${Math.round(age / 60_000)}m`}`;

  return (
    <span
      className={className}
      title={failing ? String(account.error)
        : `Updated ${never ? 'never' : `${Math.round(age / 1000)}s ago`}. Positions and balance come from Perpl's own account stream; a chain read checks them every 30s.`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        fontSize: 11.5, color: 'var(--dim, #8a8f98)', fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        aria-hidden
        style={{
          width: 6, height: 6, borderRadius: 999, background: color,
          boxShadow: !never && !stale && !failing ? `0 0 0 3px color-mix(in srgb, ${color} 22%, transparent)` : 'none',
        }}
      />
      {text}
    </span>
  );
}
