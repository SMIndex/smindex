import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { getAvailability } from '@/lib/copyAutoApi';
import AutoCopySetupModal from '@/components/copy/AutoCopySetupModal';

// "Auto-copy" next to "Copy" on trader profile + Discover cards (spec 2.11).
// Disabled with the server's reason when unavailable (not allowlisted, all
// real-time slots taken by auto-copied traders, yourself, not logged in).

export default function AutoCopyButton({ leaderWallet, leaderName, className }: {
  leaderWallet: string; leaderName?: string; className?: string;
}) {
  const { address, isAuthenticated } = useAuth();
  const [open, setOpen] = useState(false);
  const avail = useQuery({
    queryKey: ['copy-auto-avail', leaderWallet],
    queryFn: () => getAvailability(leaderWallet),
    enabled: !!isAuthenticated && !!leaderWallet,
    staleTime: 30_000,
  });
  const reason = !isAuthenticated ? 'Connect your wallet to use auto-copy'
    : avail.isLoading ? 'Checking…' : avail.data && !avail.data.available ? avail.data.reason : '';
  const enabled = !!isAuthenticated && !!avail.data?.available;
  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); if (enabled) setOpen(true); }}
        disabled={!enabled}
        title={reason || 'Copy this trader automatically (Shadow first)'}
        aria-label={enabled ? 'Auto-copy this trader' : `Auto-copy unavailable: ${reason}`}
        className={clsx(className ?? 'text-xs font-semibold px-3 py-2 rounded-lg border min-h-[44px]',
          enabled ? 'bg-accent/15 text-accent border-accent/30' : 'bg-bg-secondary text-text-secondary/60 border-text-secondary/15 cursor-not-allowed')}
      >
        Auto-copy
      </button>
      {!enabled && reason && reason !== 'Checking…' && (
        <span className="sr-only">{reason}</span>
      )}
      {open && address && (
        <AutoCopySetupModal isOpen={open} onClose={() => setOpen(false)} leaderWallet={leaderWallet}
                            leaderName={leaderName} address={address} />
      )}
    </>
  );
}
