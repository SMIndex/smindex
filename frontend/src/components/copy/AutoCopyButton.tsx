import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/components/common/Toast';
import { getAvailability, getAutoSettings } from '@/lib/copyAutoApi';
import AutoCopySetupModal from '@/components/copy/AutoCopySetupModal';

// "Auto-copy" next to "Copy" on trader profile + Discover cards (spec 2.11).
// Disabled with the server's reason when unavailable (not allowlisted, all
// real-time slots taken by auto-copied traders, yourself, not logged in).

export type AutoCopyPreset = { halfSize?: boolean; stopPct?: number; note?: string };

export default function AutoCopyButton({ leaderWallet, leaderName, className, label, preset }: {
  leaderWallet: string; leaderName?: string; className?: string;
  /** button text (default "Auto-copy"); the Unusual timing page says "Copy (half size)" */
  label?: string;
  /** opens the sheet with margin at half the user's default and a price stop (percent), spec phase 2 copy defaults */
  preset?: AutoCopyPreset;
}) {
  const { address, isAuthenticated } = useAuth();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  // Ask the server only when the user shows interest in THIS trader (hover,
  // focus or click). It used to ask for every row on mount: ~50 requests per
  // Discover load, 9.9 s each on average while they queued (2026-10-12).
  const [asked, setAsked] = useState(false);
  // Closed test (owner 2026-11-03): the server decides who sees Auto-copy at all.
  // One shared query per page; nothing renders for signed-out users or until the answer is in.
  const vis = useQuery({ queryKey: ['copy-auto-settings'], queryFn: getAutoSettings, enabled: !!isAuthenticated, staleTime: 60_000 });
  const visible = !!isAuthenticated && vis.data?.ui_visible === true;
  const avail = useQuery({
    queryKey: ['copy-auto-avail', leaderWallet],
    queryFn: () => getAvailability(leaderWallet),
    enabled: !!isAuthenticated && !!leaderWallet && asked,
    staleTime: 30_000,
  });
  const known = avail.data;
  const reason = !isAuthenticated ? 'Connect your wallet to use auto-copy'
    : avail.isFetching && !known ? 'Checking…' : known && !known.available ? known.reason : '';
  // Until the answer is in, the button is clickable; the click does the check.
  const enabled = !!isAuthenticated && (known ? !!known.available : true);
  const onClick = async (e: { stopPropagation: () => void }) => {
    e.stopPropagation();
    if (!isAuthenticated) return;
    setAsked(true);
    const d = known ?? (await avail.refetch()).data;
    if (d?.available) setOpen(true);
    else toast.error(d?.reason ? `Auto-copy unavailable: ${d.reason}` : 'Could not check auto-copy right now. Try again.');
  };
  if (!visible) return null;
  return (
    <>
      <button
        onClick={onClick}
        onMouseEnter={() => setAsked(true)}
        onFocus={() => setAsked(true)}
        disabled={!enabled}
        title={reason || 'Copy this trader automatically (Shadow first)'}
        aria-label={enabled ? 'Auto-copy this trader' : `Auto-copy unavailable: ${reason}`}
        className={clsx(className ?? 'text-xs font-semibold px-3 py-2 rounded-lg border min-h-[44px]',
          enabled ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary/60 border-text-secondary/15 cursor-not-allowed')}
      >
        {label ?? 'Auto-copy'}
      </button>
      {!enabled && reason && reason !== 'Checking…' && (
        <span className="sr-only">{reason}</span>
      )}
      {open && address && (
        <AutoCopySetupModal isOpen={open} onClose={() => setOpen(false)} leaderWallet={leaderWallet}
                            leaderName={leaderName} address={address} preset={preset} />
      )}
    </>
  );
}
