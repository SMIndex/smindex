/**
 * Which build is this? (2026-10-09)
 *
 * Compiled in by vite (`define` in vite.config.ts) so there is no way for the
 * value to drift from the bundle it ships in. Shown in the footer of every
 * page and published on `window.__SMINDEX_BUILD__`, so "am I looking at the
 * new build?" is answerable from the page itself instead of guessed.
 */
declare const __BUILD_COMMIT__: string;
declare const __BUILD_TIME__: string;

export const BUILD_COMMIT: string = typeof __BUILD_COMMIT__ === 'string' ? __BUILD_COMMIT__ : 'dev';
export const BUILD_TIME: string = typeof __BUILD_TIME__ === 'string' ? __BUILD_TIME__ : '';

/** "a1b2c3d · 09 Oct 14:02": short enough for a footer line. */
export function buildLabel(): string {
  if (!BUILD_TIME) return BUILD_COMMIT;
  const d = new Date(BUILD_TIME);
  if (Number.isNaN(d.getTime())) return BUILD_COMMIT;
  const when = d.toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  });
  return `${BUILD_COMMIT} · ${when}`;
}

try {
  (window as any).__SMINDEX_BUILD__ = { commit: BUILD_COMMIT, built: BUILD_TIME };
} catch { /* SSR / worker */ }
