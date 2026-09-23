import { useEffect, useState } from 'react';

// Design B rollout (wrapper Rule 1): a second shell selected by `?design=b`,
// persisted to localStorage. Default is 'a' (the current UI) — real users see
// NOTHING change until the owner flips the default. Reading `?design=a`
// clears back to A. This is the ONLY switch the flag controls.
const KEY = 'perpl-design';
export type DesignShell = 'a' | 'b';

// SMINDEX launch: on the product host, Design B is the ONLY design. The
// ?design param is ignored there and shell A is unreachable — there is no way
// to land on the legacy shell from smindex.xyz, including via a stored
// preference set earlier on another host.
export const BRAND_HOSTS = ['smindex.xyz', 'www.smindex.xyz'];

export function isBrandHost(host?: string): boolean {
  const h = (host ?? (typeof window !== 'undefined' ? window.location.hostname : '')).toLowerCase();
  return BRAND_HOSTS.includes(h);
}

function readShell(): DesignShell {
  try {
    if (isBrandHost()) return 'b';        // host rule wins over param and storage
    const p = new URLSearchParams(window.location.search).get('design');
    if (p === 'b' || p === 'a') {
      localStorage.setItem(KEY, p);
      return p;
    }
    const stored = localStorage.getItem(KEY);
    return stored === 'b' ? 'b' : 'a';
  } catch {
    return 'a';
  }
}

export function useDesignShell(): DesignShell {
  const [shell, setShell] = useState<DesignShell>(readShell);
  useEffect(() => {
    // re-read on navigation (the query param can change without a remount)
    const onNav = () => setShell(readShell());
    window.addEventListener('popstate', onNav);
    return () => window.removeEventListener('popstate', onNav);
  }, []);
  return shell;
}

// Shell-B theme — INDEPENDENT from shell A's `perpl-theme` (default light per
// the prototype), so migrating B never disturbs A's dark-default theme.
const THEME_KEY = 'perpl-design-theme';
export type DesignTheme = 'light' | 'dark';

export function useDesignTheme(): [DesignTheme, () => void] {
  const [theme, setTheme] = useState<DesignTheme>(() => {
    try {
      return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light';
    } catch {
      return 'light';
    }
  });
  const toggle = () =>
    setTheme((t) => {
      const next = t === 'dark' ? 'light' : 'dark';
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        /* private mode */
      }
      return next;
    });
  return [theme, toggle];
}
