# Mobile / PWA / App-shell Changelog

## 2026-09-30 — Filter/sort bottom sheets + 44px floor; Strategies nav removed
**Files**: `frontend/src/design-b/MobileSheetB.tsx` (new), `design-b/tokens.css`, `design-b/screens/{AnalyticsPulseB,AnalyticsMoversB,DiscoverB,AnalyticsAssetB}.tsx`, `design-b/ShellB.tsx`, `App.tsx`, `frontend/audit_qqex/mobile_390.py` + `mobile_0930/`.
**Why**: owner Parts 2.1 and 3.4.
**What**:
- **Bottom sheets:** on phones, the filter/sort controls of pulse, movers, Discover and the asset page open in a bottom sheet from one 44 px button; desktop is unchanged.
- **44 px floor:** every shell-B control is at least 44 px on phones. Result: 0 buttons under 44 px on 12 screens × 2 themes (was 1–89), 0 px overflow.
- **Strategies:** removed from the sidebar; `/strategies*` → `/analytics`.

## 2026-09-29 — Mobile pass per spec Part 4 (shell B)
**Files**: `frontend/src/design-b/ShellB.tsx` (`MOBILE_TABS`, menu, reconnect pill), `design-b/useReconnectOnWake.ts`, `design-b/tokens.css`, `index.css`, `components/common/Toast.tsx`, `lib/mobileConfirm.ts`, `components/common/MobileConfirmSheet.tsx`, `App.tsx`, `design-b/screens/AnalyticsPulseB.tsx`, `frontend/audit_qqex/mobile_390.py` + `mobile_0929/`.
**Why**: overnight prompt, Part D.
**What**:
- **Navigation:** 5 bottom tabs (Analytics/Copy/Trade/Explorer/Portfolio); wallet, theme and settings in the top-bar menu; Strategies off mobile nav.
- **Confirmation sheet:** one sheet with lifecycle for every mobile order/close/cancel/TP-SL (desktop resolves instantly).
- **Layout:** toasts above the tab bar; safe areas; lazy Trade chart; reconnect on wake.
- **Verified:** 0 px overflow on 12 screens × 2 themes at 390 px.
- **Partial:** filter/sort bottom sheets and secondary controls under 44 px.

## 2026-07-13 — Fix: users stuck on stale bundles after deploys (no cache headers + SW heuristic caching)

**Files modified**
- `frontend/public/sw.js` (repo)
- `/etc/nginx/sites-available/the legacy host` (server only — backup `/root/nginx_terminal_backup_*.conf`)

**Why**
User's desktop app (installed PWA) kept running an old bundle even after hard
refresh — console showed chunks that no longer exist on the server. Root cause:
nginx sent NO Cache-Control headers, so browsers heuristically cached
index.html (~10% of file age ≈ up to a day). The SW's SPA-navigation handler
`fetch('/index.html')` obeys HTTP cache, so installed-app windows kept getting
the stale HTML → stale chunk references → old code (which lacked the 429 toast
and click guard).

**What changed**
- nginx: `location = /index.html` and `= /sw.js` → `Cache-Control: no-cache,
  must-revalidate`; `location /assets/` → `public, max-age=31536000, immutable`
  (safe — asset filenames are content-hashed). SPA routes inherit the
  index.html header via try_files internal redirect (verified).
- sw.js: CACHE_NAME perpl-v5→v6 (purges old asset cache on activate);
  SPA navigation now `fetch('/index.html', { cache: 'no-cache' })` so a deploy
  is picked up immediately.

**Deploy/verify**
- sw.js scp'd into live dist (vite copies public/ verbatim on future builds);
  nginx -t + reload. curl-verified: /, /index.html, /sw.js, /copy/discover all
  `no-cache, must-revalidate`; asset `immutable`; served sw.js is v6.
- Existing stuck clients: close ALL app/site windows and reopen (browser
  refetches sw.js on navigation, ignoring HTTP cache by default → v6 installs →
  fresh index). Worst case: DevTools → Application → Clear site data, or
  reinstall the desktop app. Future deploys propagate automatically.
