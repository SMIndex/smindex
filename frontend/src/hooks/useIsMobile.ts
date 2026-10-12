import { useState, useEffect } from 'react';

// Matches Tailwind's `md` breakpoint (768px). Below it we render the mobile
// Copy-Trading PWA UI; at/above it the existing desktop pages render unchanged.
// SPA-only (no SSR) so `window.matchMedia` is always available on first render.
const MOBILE_QUERY = '(max-width: 767px)';

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() =>
    typeof window !== 'undefined' ? window.matchMedia(MOBILE_QUERY).matches : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    // Sync immediately in case it changed between initial state and effect.
    setIsMobile(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return isMobile;
}
