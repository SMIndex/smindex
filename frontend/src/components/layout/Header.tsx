import { useState, useRef, useEffect, useCallback } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { clsx } from 'clsx';
import WalletButton from '@/components/auth/WalletButton';
import { useTheme } from '@/hooks/useTheme';
import { useNotificationStore } from '@/stores/notificationStore';
import { useSoundStore } from '@/stores/soundStore';
import NotificationPanel from '@/components/common/NotificationPanel';

interface NavItem {
  to: string;
  label: string;
  icon?: string;
}

interface NavGroup {
  label: string;
  icon: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    label: 'Trade',
    icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6',
    items: [
      { to: '/trade', label: 'Terminal' },
      { to: '/orders', label: 'Orders' },
    ],
  },
  {
    label: 'More',
    icon: 'M4 6h16M4 12h16M4 18h16',
    items: [
      { to: '/markets', label: 'Markets' },
      { to: '/insights', label: 'Wallet Insights' },
      { to: '/analytics', label: 'Analytics' },
      { to: '/heatmap', label: 'Heatmap' },
      { to: '/multi-chart', label: 'Multi-Chart' },
      { to: '/journal', label: 'Journal' },
      { to: '/social', label: 'Social Feed' },
      { to: '/compare', label: 'Compare' },
      // { to: '/paper', label: 'Paper Trade' }, // temporarily disabled
      { to: '/settings', label: 'Settings' },
    ],
  },
];

const topLevelLinks: NavItem[] = [
  { to: '/portfolio', label: 'Portfolio', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
  { to: '/copy', label: 'Copy Trade', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z' },
];

function NavDropdown({ group }: { group: NavGroup }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const location = useLocation();

  const isGroupActive = group.items.some((item) =>
    location.pathname.startsWith(item.to),
  );

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={clsx(
          'h-9 px-3 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5',
          isGroupActive
            ? 'bg-accent/10 text-accent'
            : 'text-text-secondary hover:text-text-primary hover:bg-text-secondary/10',
        )}
      >
        <svg className="w-3.5 h-3.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={group.icon} />
        </svg>
        {group.label}
        <svg className={clsx('w-3 h-3 opacity-50 transition-transform duration-200', open && 'rotate-180')} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1.5 w-48 bg-bg-card/95 backdrop-blur-xl border border-text-secondary/15 rounded-xl shadow-2xl shadow-black/30 py-1.5 z-50 animate-[fadeIn_0.15s_ease-out]">
          {group.items.map((item) => {
            const isActive = location.pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                onClick={() => setOpen(false)}
                className={clsx(
                  'flex items-center gap-2 mx-1.5 px-3 py-2 rounded-lg text-[13px] transition-all',
                  isActive
                    ? 'text-accent bg-accent/10'
                    : 'text-text-secondary hover:text-text-primary hover:bg-text-secondary/10',
                )}
              >
                {isActive && <div className="w-1 h-1 rounded-full bg-accent" />}
                {item.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Phantom-style bottom tab bar — icons only, active indicator line, glass bg
const MOBILE_TABS = [
  { to: '/', icon: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6' },
  { to: '/trade', icon: 'M13 7h8m0 0v8m0-8l-8 8-4-4-6 6' },
  { to: '/copy', icon: 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z' },
  { to: '/portfolio', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
  { to: '/more', icon: 'M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z' },
];

const MORE_LINKS = [
  { to: '/markets', label: 'Markets', icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm0 8a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6z' },
  { to: '/heatmap', label: 'Heatmap', icon: 'M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z' },
  { to: '/analytics', label: 'Analytics', icon: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z' },
  { to: '/orders', label: 'Orders', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
  { to: '/multi-chart', label: 'Multi-Chart', icon: 'M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zm10 0a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z' },
  { to: '/journal', label: 'Journal', icon: 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z' },
  { to: '/social', label: 'Social', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
  { to: '/compare', label: 'Compare', icon: 'M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4' },
  // { to: '/paper', label: 'Paper Trade', icon: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z' }, // temporarily disabled
  { to: '/settings', label: 'Settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z' },
];

export function MobileTabBar() {
  const location = useLocation();
  const [showMore, setShowMore] = useState(false);

  // Close menu when route changes
  useEffect(() => { setShowMore(false); }, [location.pathname]);

  const isMoreActive = MORE_LINKS.some((l) => location.pathname.startsWith(l.to));

  return (
    <div className="md:hidden fixed bottom-0 left-0 right-0 z-50">
      {/* More popup — rendered via portal-like approach to avoid z-index issues */}
      {showMore && (
        <div className="fixed inset-0 z-[100]" onClick={() => setShowMore(false)}>
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/50" />
          {/* Popup */}
          <div
            className="absolute bottom-[60px] left-3 right-3 bg-bg-card border border-text-secondary/10 rounded-2xl shadow-2xl overflow-hidden animate-fadeInUp"
            style={{ marginBottom: 'env(safe-area-inset-bottom, 0px)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="grid grid-cols-3 gap-px p-1.5">
              {MORE_LINKS.map((link) => {
                const active = location.pathname.startsWith(link.to);
                return (
                  <Link
                    key={link.to}
                    to={link.to}
                    onClick={() => setShowMore(false)}
                    className={clsx(
                      'flex flex-col items-center gap-1.5 py-3.5 rounded-xl transition-all active:scale-95',
                      active ? 'text-accent bg-accent/10' : 'text-text-secondary active:bg-text-secondary/10',
                    )}
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={link.icon} />
                    </svg>
                    <span className="text-[10px] font-medium">{link.label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Tab bar — solid bg, not transparent */}
      <nav className="bg-bg-secondary border-t border-text-secondary/10" style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}>
        <div className="flex items-center justify-around h-[52px]">
          {MOBILE_TABS.map((tab) => {
            if (tab.to === '/more') {
              return (
                <button
                  key="more"
                  onClick={() => setShowMore(!showMore)}
                  className={clsx(
                    'flex items-center justify-center w-14 h-full transition-all active:scale-90',
                    isMoreActive || showMore ? 'text-accent' : 'text-text-secondary/60',
                  )}
                >
                  <svg className="w-[22px] h-[22px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={tab.icon} />
                  </svg>
                </button>
              );
            }

            const isActive = tab.to === '/' ? location.pathname === '/' : location.pathname.startsWith(tab.to);
            return (
              <Link
                key={tab.to}
                to={tab.to}
                className={clsx(
                  'relative flex items-center justify-center w-14 h-full transition-all active:scale-90',
                  isActive ? 'text-accent' : 'text-text-secondary/60',
                )}
              >
                <svg className="w-[22px] h-[22px]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={isActive ? 2.5 : 1.5} d={tab.icon} />
                </svg>
                {/* Indicator line below icon */}
                {isActive && (
                  <div className="absolute bottom-1 left-4 right-4 h-[3px] bg-accent rounded-full" />
                )}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      onClick={toggleTheme}
      className="w-8 h-8 rounded-lg flex items-center justify-center text-text-secondary hover:text-accent hover:bg-accent/10 transition-all duration-300"
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      {theme === 'dark' ? (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
        </svg>
      ) : (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
        </svg>
      )}
    </button>
  );
}

function SoundToggle() {
  const { enabled, toggle } = useSoundStore();
  return (
    <button
      onClick={toggle}
      className="w-8 h-8 rounded-lg flex items-center justify-center text-text-secondary hover:text-accent hover:bg-accent/10 transition-all duration-300"
      title={enabled ? 'Mute sounds' : 'Enable sounds'}
    >
      {enabled ? (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15.536 8.464a5 5 0 010 7.072M17.95 6.05a8 8 0 010 11.9M6.5 8.5H4a1 1 0 00-1 1v5a1 1 0 001 1h2.5l4.5 4V4.5l-4.5 4z" />
        </svg>
      ) : (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707A1 1 0 0112 5v14a1 1 0 01-1.707.707L5.586 15zM17 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2" />
        </svg>
      )}
    </button>
  );
}

function NotificationBell() {
  const [open, setOpen] = useState(false);
  const unreadCount = useNotificationStore((s) => s.unreadCount);
  const onClose = useCallback(() => setOpen(false), []);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="w-8 h-8 rounded-lg flex items-center justify-center text-text-secondary hover:text-accent hover:bg-accent/10 transition-all duration-300 relative"
        title="Notifications"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-4 h-4 bg-danger text-white text-[9px] font-bold rounded-full flex items-center justify-center">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>
      <NotificationPanel open={open} onClose={onClose} />
    </div>
  );
}

export default function Header() {
  const location = useLocation();

  return (
    <header className="rd-sans sticky top-0 z-50 border-b" style={{ paddingTop: 'env(safe-area-inset-top, 0px)', background: 'color-mix(in srgb, var(--bg) 82%, transparent)', borderColor: 'var(--border)', backdropFilter: 'blur(16px)' }}>
      <div className="px-3 md:px-5 h-14 flex items-center">
        {/* Logo — Perpl candlestick mark + wordmark */}
        <Link to="/" className="flex items-center gap-2.5 mr-3 md:mr-8 shrink-0">
          <svg width="20" height="20" viewBox="0 0 200 200" fill="none" aria-hidden>
            <rect x="24" y="30" width="34" height="150" rx="11" fill="var(--accent)" />
            <rect x="84" y="12" width="34" height="96" rx="11" fill="var(--accent-2)" />
            <rect x="144" y="74" width="34" height="110" rx="11" fill="var(--accent)" />
          </svg>
          <span className="rd-mono font-extrabold text-[15px] hidden sm:block" style={{ color: 'var(--text)', letterSpacing: '-.5px' }}>
            Perpl
          </span>
        </Link>

        {/* Center nav */}
        <nav className="hidden md:flex items-center gap-0.5 flex-1">
          <Link
            to="/"
            className={clsx(
              'h-9 px-3 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5',
              location.pathname === '/'
                ? 'bg-accent/10 text-accent'
                : 'text-text-secondary hover:text-text-primary hover:bg-text-secondary/10',
            )}
          >
            <svg className="w-3.5 h-3.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
            </svg>
            Dashboard
          </Link>

          <div className="w-px h-5 bg-text-secondary/10 mx-1" />

          {navGroups.slice(0, 1).map((group) => (
            <NavDropdown key={group.label} group={group} />
          ))}

          {topLevelLinks.map((link) => (
            <Link
              key={link.to}
              to={link.to}
              className={clsx(
                'h-9 px-3 rounded-lg text-[13px] font-medium transition-all flex items-center gap-1.5',
                location.pathname.startsWith(link.to)
                  ? 'bg-accent/10 text-accent'
                  : 'text-text-secondary hover:text-text-primary hover:bg-text-secondary/10',
              )}
            >
              {link.icon && (
                <svg className="w-3.5 h-3.5 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d={link.icon} />
                </svg>
              )}
              {link.label}
            </Link>
          ))}

          <div className="w-px h-5 bg-text-secondary/10 mx-1" />

          {navGroups.slice(1).map((group) => (
            <NavDropdown key={group.label} group={group} />
          ))}
        </nav>

        {/* Right side — desktop: all buttons, mobile: just notifications + wallet */}
        <div className="flex items-center gap-1 md:gap-1.5 ml-auto">
          <div className="hidden md:flex items-center gap-1">
            <SoundToggle />
            <ThemeToggle />
          </div>
          {/* Theme toggle on mobile too (desktop shows it in the cluster above) */}
          <div className="md:hidden">
            <ThemeToggle />
          </div>
          <NotificationBell />
          <WalletButton />
        </div>
      </div>

    </header>
  );
}
