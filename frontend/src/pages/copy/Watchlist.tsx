import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import { useWatchlist, useSubscriptions } from '@/hooks/useCopyV1';
import CopyLayout from '@/components/copy/CopyLayout';
import ConnectPrompt from '@/components/copy/ConnectPrompt';
import TraderProfileModal from '@/components/copy/TraderProfileModal';
import { getActiveSummaries, type ActivePositionSummary } from '@/lib/copyApi';
import { shortenAddress } from '@/lib/formatters';
import { useIsMobile } from '@/hooks/useIsMobile';
import MobileCopy from '@/components/copy/mobile/MobileCopy';

function ActiveBadge({ s }: { s?: ActivePositionSummary }) {
  const count = s?.active_positions_count;
  if (count != null && count > 0) {
    return (
      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-success/12 text-success border border-success/25 whitespace-nowrap">
        {count} active · {s!.active_markets.slice(0, 3).map((m) => m.symbol).join(', ')}
      </span>
    );
  }
  if (count === 0) return <span className="text-[10px] px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/60 whitespace-nowrap">No active trades</span>;
  if (!s) return <span className="text-[10px] px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/50 animate-pulse whitespace-nowrap">Checking…</span>;
  return <span className="text-[10px] px-2 py-0.5 rounded-full bg-text-secondary/8 text-text-secondary/50 whitespace-nowrap">Unavailable</span>;
}

function WatchlistInner() {
  const navigate = useNavigate();
  const { items, loading, error, unwatch, watch, isWatched } = useWatchlist(true);
  const subs = useSubscriptions(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, ActivePositionSummary>>({});
  const [profileWallet, setProfileWallet] = useState<string | null>(null);
  const onWatchToggle = async (w: string) => { if (isWatched(w)) await unwatch(w); else await watch(w); };

  // Live active-position counts for watched traders (batch endpoint, cached server-side).
  useEffect(() => {
    if (!items.length) return;
    let cancelled = false;
    getActiveSummaries(items.map((i) => i.trader_wallet))
      .then((s) => { if (!cancelled) setSummaries(s); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [items]);

  const handleUnwatch = async (wallet: string) => {
    setBusy(wallet);
    try {
      await unwatch(wallet);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl bg-bg-card border border-text-secondary/10 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text-primary">Watchlist</h3>
        <span className="text-[11px] text-text-secondary/50">Read-only interest — separate from copy. Watching never places an order.</span>
      </div>

      {error && <div className="px-3 py-2 rounded-lg bg-danger/10 border border-danger/25 text-xs text-danger mb-3">{error}</div>}

      {loading ? (
        <div className="text-center text-xs text-text-secondary/60 py-10">Loading watchlist…</div>
      ) : items.length === 0 ? (
        <div className="text-center text-xs text-text-secondary/60 py-10">
          You're not watching anyone yet. Find traders on{' '}
          <button onClick={() => navigate('/copy/discover')} className="text-accent hover:underline">Discover</button>.
        </div>
      ) : (
        <div className="divide-y divide-text-secondary/5">
          {items.map((w) => (
            <div key={w.id} className="flex items-center justify-between gap-3 py-3">
              <button
                onClick={() => setProfileWallet(w.trader_wallet)}
                className="flex items-center gap-3 min-w-0 text-left group"
              >
                <div className="w-8 h-8 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center shrink-0">
                  <svg className="w-4 h-4 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                </div>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary group-hover:text-accent transition-colors truncate">
                      {w.display_name || shortenAddress(w.trader_wallet)}
                    </span>
                    <ActiveBadge s={summaries[w.trader_wallet.toLowerCase()]} />
                  </div>
                  <div className="text-[11px] text-text-secondary/60 font-mono">{shortenAddress(w.trader_wallet)}</div>
                </div>
              </button>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => setProfileWallet(w.trader_wallet)}
                  className="text-xs font-medium py-1.5 px-3 rounded-lg bg-bg-secondary text-text-secondary hover:text-text-primary transition-colors"
                >
                  View Profile
                </button>
                <button
                  onClick={() => handleUnwatch(w.trader_wallet)}
                  disabled={busy === w.trader_wallet}
                  className="text-xs font-medium py-1.5 px-3 rounded-lg bg-bg-secondary text-danger hover:bg-danger/10 transition-colors disabled:opacity-50"
                >
                  Unwatch
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {profileWallet && (
        <TraderProfileModal
          wallet={profileWallet}
          isWatched={isWatched(profileWallet)}
          onWatchToggle={onWatchToggle}
          onCreateSub={subs.create}
          onClose={() => setProfileWallet(null)}
        />
      )}
    </div>
  );
}

export default function WatchlistPage() {
  const isMobile = useIsMobile();
  const { isAuthenticated } = useAuth();
  if (isMobile) return <MobileCopy initialTab="watchlist" />;
  return (
    <CopyLayout>
      {isAuthenticated ? <WatchlistInner /> : <ConnectPrompt message="Connect to view and manage your watchlist." />}
    </CopyLayout>
  );
}
