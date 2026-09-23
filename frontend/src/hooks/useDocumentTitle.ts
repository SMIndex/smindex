import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

// SMINDEX launch — per-page document titles. Presentation only: it reads the
// pathname and writes document.title. No route is added, renamed or changed.
const BRAND = 'SMINDEX';

const TITLES: [RegExp, string][] = [
  [/^\/$/, 'Dashboard'],
  [/^\/trade/, 'Trade'],
  [/^\/copy\/discover/, 'Discover traders'],
  [/^\/copy\/watchlist/, 'Watchlist'],
  [/^\/copy\/dashboard/, 'Copy dashboard'],
  [/^\/copy\/trader\//, 'Trader'],
  [/^\/copy\/portfolio/, 'Copy portfolio'],
  [/^\/copy\/history/, 'Copy history'],
  [/^\/copy/, 'Copy trade'],
  [/^\/analytics\/movers/, 'Movers'],
  [/^\/analytics\/wallet\//, 'Wallet explorer'],
  [/^\/analytics\/[^/]+$/, 'Analytics'],
  [/^\/analytics/, 'Analytics'],
  [/^\/wallet/, 'Wallet explorer'],
  [/^\/insights/, 'Wallet insights'],
  [/^\/portfolio/, 'Portfolio'],
  [/^\/strategies/, 'Strategies'],
  [/^\/paper/, 'Paper trade'],
  [/^\/settings/, 'Settings'],
  [/^\/stats/, 'Stats'],
];

export function titleForPath(pathname: string): string {
  for (const [re, label] of TITLES) {
    if (re.test(pathname)) return `${BRAND} — ${label}`;
  }
  return BRAND;
}

export function useDocumentTitle(): void {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = titleForPath(pathname);
  }, [pathname]);
}
