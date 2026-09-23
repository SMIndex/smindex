import { type ReactNode } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import WalletButton from '@/components/auth/WalletButton';
import ExplorerSearch from './ExplorerSearch';
import { useDesignTheme } from './useDesignShell';
import { Brand } from './BrandLockup';
import './tokens.css';

// Design B app shell (prototype §2): 232px sidebar on desktop, sticky top bar +
// 5-tab bottom bar on mobile (<=820px). Presentation only — every nav item just
// navigates to the SAME existing route; nothing is deleted or renamed (Rule 1).
// Hidden entries (Dashboard, Orders, Markets, Heatmap, Multi-chart, Journal,
// Social, Compare, Paper, More, top-bar icons, markets list) keep their routes
// alive; they're simply not rendered in shell B's sidebar.

interface NavItem { label: string; short: string; to: string; match: string[]; icon: ReactNode; }

const I = {
  copy: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></svg>),
  trade: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M7 4v16M7 8h3v8H7M17 4v16M14 6h3v10h-3" /></svg>),
  analytics: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 19V5M4 19h16M8 15v-5M12 15V8M16 15v-3" /></svg>),
  portfolio: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="6" width="18" height="14" rx="2" /><path d="M3 10h18M8 6V4M16 6V4" /></svg>),
  settings: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" /></svg>),
  strategies: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 4v16M4 14l4-4 4 3 6-7" /><circle cx="8" cy="10" r="1.4" /><circle cx="12" cy="13" r="1.4" /><circle cx="18" cy="6" r="1.4" /></svg>),
  explorer: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></svg>),
};

const NAV: NavItem[] = [
  { label: 'Copy trade', short: 'Copy', to: '/copy/discover', match: ['/copy'], icon: I.copy },
  { label: 'Strategies', short: 'Strategies', to: '/strategies', match: ['/strategies'], icon: I.strategies },
  { label: 'Terminal', short: 'Terminal', to: '/trade', match: ['/trade'], icon: I.trade },
  { label: 'Analytics', short: 'Analytics', to: '/analytics', match: ['/analytics'], icon: I.analytics },
  // Wallet Explorer (Part 2) — under the Markets label with Analytics
  { label: 'Explorer', short: 'Explorer', to: '/wallet', match: ['/wallet'], icon: I.explorer },
  { label: 'Portfolio', short: 'Portfolio', to: '/portfolio', match: ['/portfolio'], icon: I.portfolio },
  { label: 'Settings', short: 'Settings', to: '/settings', match: ['/settings'], icon: I.settings },
];

// Routes that have a migrated B screen. On these the shell uses B's own theme
// toggle. On UNMIGRATED routes the content is the existing (A) page, which
// styles itself against shell A's theme — so the B chrome mirrors A's actual
// <html> theme there (keeps unmigrated content readable, and never writes A's
// theme state). Grows as screens migrate.
// Precise prefixes: only the migrated copy routes use B's own theme. The
// unmigrated copy sub-tabs (/copy/watchlist, /dashboard, /portfolio, /history)
// stay in mirror mode so their A content stays readable.
const MIGRATED_PREFIXES = ['/settings', '/analytics', '/copy/discover', '/copy/trader', '/portfolio', '/trade', '/strategies', '/wallet'];

export default function ShellB({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [theme, toggleTheme] = useDesignTheme();
  const isActive = (n: NavItem) => n.match.some((m) => location.pathname.startsWith(m));

  const migrated = MIGRATED_PREFIXES.some((p) => location.pathname.startsWith(p));
  // A's live theme (shell A default is dark; .light on <html> when light).
  let aTheme: 'light' | 'dark' = 'dark';
  try { aTheme = document.documentElement.classList.contains('light') ? 'light' : 'dark'; } catch { /* ssr */ }
  // Migrated B screens use B's toggle; unmigrated (A content) mirror A's theme
  // so the surrounding chrome background matches the readable A page.
  const shellTheme = migrated ? theme : aTheme;

  return (
    <div className="dsb" data-dsb-theme={shellTheme}>
      <div className="shell">
        {/* desktop sidebar */}
        <aside className="side">
          <div className="brand"><Brand theme={shellTheme} height={26} /></div>
          {/* wallet address search (Wallet Explorer Part 2) */}
          <div style={{ padding: '4px 0 8px' }}><ExplorerSearch compact /></div>
          <div className="label">Trading</div>
          {NAV.slice(0, 3).map((n) => (
            <button key={n.to} className="navb" aria-selected={isActive(n)} onClick={() => navigate(n.to)}>{n.icon}{n.label}</button>
          ))}
          <div className="label">Markets</div>
          {NAV.slice(3, 5).map((n) => (
            <button key={n.to} className="navb" aria-selected={isActive(n)} onClick={() => navigate(n.to)}>{n.icon}{n.label}</button>
          ))}
          <div className="label">User</div>
          {NAV.slice(5).map((n) => (
            <button key={n.to} className="navb" aria-selected={isActive(n)} onClick={() => navigate(n.to)}>{n.icon}{n.label}</button>
          ))}
          <div className="foot">
            {migrated ? (
              <button className="btn ghost" onClick={toggleTheme}>
                {theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
              </button>
            ) : (
              <span className="btn ghost" style={{ cursor: 'default', fontSize: 12 }} title="Theme follows the app until this screen is redesigned">Theme: {shellTheme}</span>
            )}
            <WalletButton />
          </div>
        </aside>

        {/* mobile top bar (+ address search — Explorer's mobile entry point) */}
        <div className="mtop">
          {/* mark alone: the bar also carries search + wallet, so the
              full lockup would crowd it at phone widths */}
          <div className="brand"><Brand theme={shellTheme} compact height={22} /></div>
          <div className="right" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <ExplorerSearch compact />
            <WalletButton />
          </div>
        </div>

        <main className="content">{children}</main>

        {/* mobile bottom tabs (Explorer reached via the top-bar search — 7 tabs don't fit) */}
        <nav className="mtab" role="tablist" aria-label="Main">
          {NAV.filter((n) => n.to !== '/wallet').map((n) => (
            <button key={n.to} role="tab" aria-selected={isActive(n)} onClick={() => navigate(n.to)}>{n.icon}{n.short}</button>
          ))}
        </nav>
      </div>
    </div>
  );
}
