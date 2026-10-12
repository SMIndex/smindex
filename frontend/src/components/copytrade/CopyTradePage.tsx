import { useState } from 'react';
import { clsx } from 'clsx';
import { useAuth } from '@/hooks/useAuth';
import LeaderBoard from '@/components/copytrade/LeaderBoard';
import MyPositions from '@/components/copytrade/MyPositions';
import MyCopies from '@/components/copytrade/MyCopies';
import FollowingFeed from '@/components/copytrade/FollowingFeed';

type Tab = 'leaderboard' | 'following' | 'my-copies' | 'my-positions';

const TABS: { key: Tab; label: string; icon: string }[] = [
  {
    key: 'leaderboard',
    label: 'Leaderboard',
    icon: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
  },
  {
    key: 'following',
    label: 'Following',
    icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z',
  },
  {
    key: 'my-copies',
    label: 'My Copies',
    icon: 'M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2',
  },
  {
    key: 'my-positions',
    label: 'Positions',
    icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
  },
];

const GUIDE_DISMISSED_KEY = 'perpl-copytrade-guide-dismissed';

export default function CopyTradePage() {
  const [tab, setTab] = useState<Tab>('leaderboard');
  const [guideDismissed, setGuideDismissed] = useState(() => localStorage.getItem(GUIDE_DISMISSED_KEY) === '1');
  const { isConnected } = useAuth();

  const dismissGuide = () => {
    localStorage.setItem(GUIDE_DISMISSED_KEY, '1');
    setGuideDismissed(true);
  };

  return (
    <div className="space-y-5 animate-fadeIn">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary flex items-center gap-2">
            Copy Trading
            <span className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              LIVE
            </span>
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Follow top traders and mirror their positions on Perpl
          </p>
        </div>
      </div>

      {/* How it works guide */}
      {!guideDismissed && (
        <div className="flex items-center justify-between gap-4 px-4 py-3 rounded-lg bg-accent/5 border border-accent/15 text-xs text-text-secondary">
          <div className="flex flex-wrap items-center gap-1">
            <span className="font-semibold text-text-primary mr-1">How Copy Trading Works:</span>
            <span>1. Browse the leaderboard</span>
            <span className="text-accent mx-0.5">&rarr;</span>
            <span>2. Follow a trader</span>
            <span className="text-accent mx-0.5">&rarr;</span>
            <span>3. Their trades appear as alerts</span>
            <span className="text-accent mx-0.5">&rarr;</span>
            <span>4. Copy with one click</span>
          </div>
          <button onClick={dismissGuide} className="shrink-0 text-text-secondary hover:text-text-primary transition-colors" title="Dismiss">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="flex items-center gap-1 p-1 bg-bg-secondary/80 backdrop-blur-sm rounded-xl border border-text-secondary/5 overflow-x-auto scrollbar-hide">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx(
              'flex items-center gap-1.5 sm:gap-2 px-3 sm:px-4 py-2.5 rounded-lg text-xs sm:text-sm font-medium transition-all duration-300 whitespace-nowrap shrink-0',
              tab === t.key
                ? 'bg-accent text-white shadow-lg shadow-accent/20'
                : 'text-text-secondary hover:text-text-primary hover:bg-bg-card/50',
            )}
          >
            <svg className="w-4 h-4 hidden sm:block" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={t.icon} />
            </svg>
            {t.label}
            {t.key !== 'leaderboard' && isConnected && (
              <span className={clsx(
                'w-2 h-2 rounded-full',
                tab === t.key ? 'bg-white/80' : 'bg-success',
              )} />
            )}
          </button>
        ))}
      </div>

      {/* Tab Content with animation */}
      <div key={tab} className="animate-fadeInUp">
        {tab === 'leaderboard' && <LeaderBoard />}
        {tab === 'following' && <FollowingFeed />}
        {tab === 'my-copies' && <MyCopies />}
        {tab === 'my-positions' && <MyPositions />}
      </div>
    </div>
  );
}
