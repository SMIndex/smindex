import { type ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';
import { COPY_LIVE_ENABLED } from '@/config/constants';

const TABS: { to: string; label: string }[] = [
  { to: '/copy/discover', label: 'Discover' },
  { to: '/copy/watchlist', label: 'Watchlist' },
  { to: '/copy/dashboard', label: 'Dashboard' },
  { to: '/copy/portfolio', label: 'Portfolio' },
  { to: '/copy/history', label: 'History' },
];

export default function CopyLayout({ children }: { children: ReactNode }) {
  return (
    <div className="rd-sans space-y-5 animate-fadeIn">
      {/* Header */}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="rd-mono text-[30px] font-extrabold text-text-primary flex items-center gap-2.5" style={{ letterSpacing: '-1px' }}>
            Social Copy Trading
            <span className={clsx(
              'inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full border',
              COPY_LIVE_ENABLED
                ? 'bg-success/10 text-success border-success/20'
                : 'bg-text-secondary/10 text-text-secondary border-text-secondary/20',
            )}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', COPY_LIVE_ENABLED ? 'bg-success animate-pulse' : 'bg-text-secondary/50')} />
              {COPY_LIVE_ENABLED ? 'LIVE' : 'LIVE — OFF'}
            </span>
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Discover traders, watch them, and copy their trades live on Perpl — you confirm every order.
          </p>
        </div>
      </div>

      {/* Real-order notice */}
      <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-lg bg-danger/5 border border-danger/15 text-xs text-text-secondary">
        <svg className="w-4 h-4 shrink-0 text-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4a2 2 0 00-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
        </svg>
        <span>
          <span className="font-semibold text-text-primary">Live copy places real Perpl orders</span>
          {COPY_LIVE_ENABLED
            ? ' with your own funds. You confirm each trade before it is placed.'
            : ' — currently turned OFF, so no real orders will be placed yet.'}
        </span>
      </div>

      {/* Sub-nav */}
      <div className="flex items-center gap-1 p-1 bg-bg-secondary/80 backdrop-blur-sm rounded-xl border border-text-secondary/5 overflow-x-auto scrollbar-hide">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-2 px-4 py-2.5 rounded-lg text-xs sm:text-sm font-medium transition-all duration-300 whitespace-nowrap shrink-0',
                isActive
                  ? 'bg-accent text-white shadow-lg shadow-accent/20'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-card/50',
              )
            }
          >
            {t.label}
          </NavLink>
        ))}
      </div>

      <div className="animate-fadeInUp">{children}</div>
    </div>
  );
}
