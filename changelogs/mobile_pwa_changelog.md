# Mobile / PWA / App-shell Changelog

## 2026-10-07 — Beta basics + honest outage states
**Files**: `frontend/src/design-b/{ShellB.tsx,tokens.css,screens/LegalB.tsx,screens/SettingsB.tsx,screens/AnalyticsPulseB.tsx,screens/AnalyticsMoversB.tsx,screens/AnalyticsAssetB.tsx,screens/DiscoverB.tsx}`, `frontend/src/App.tsx`, `frontend/index.html`, `frontend/public/brent.{html,js}`.
**Why**: beta readiness 7.3/7.6/10.x.
**What**:
- **Beta basics:** BETA tag; footer (Terms / Privacy / Send feedback); `/terms`, `/privacy`, `/feedback`; feedback form in Settings.
- **Outage states:** Pulse, Movers, Asset and Discover say "unavailable — retrying" instead of "no data".
- **Sharing:** OG/Twitter card on the home page.
- **CSP:** the Brent page script moved out of the HTML so it runs under the CSP.
- **Text:** sweep cadence now says "about every 35 minutes".
- **PWA:** verified the service-worker update after a deploy (no cache clear needed).

## 2026-10-02 — Every remaining page and prompt converted to design B
**Files**: `frontend/src/design-b/{tokens.css,ShellB.tsx,useDesignShell.ts,portalRoot.ts(new),MobileSheetB.tsx}`, `frontend/src/App.tsx` (RootOverlays), `frontend/src/components/common/Modal.tsx`, `frontend/src/components/copy/CopyLayout.tsx`, `frontend/src/pages/copy/{CopyDashboard,Watchlist}.tsx`, `frontend/src/components/copy/TraderProfileModal.tsx`, `frontend/src/components/terminal/{MarketSelector,FundingComparison,TradingChart}.tsx`, `frontend/src/components/copy/mobile/{MobileCopy,MobileCopySheet,MobileTraderProfile}.tsx`, `frontend/src/hooks/useTheme.ts`, `frontend/audit_qqex/{legacy_scan.py,toggle_and_shellA.py}`.
**Why**: owner: "pages and prompts still on the old design — scan everything and convert".
**What**:
- **Bridge:** the A→B token bridge (`.abridge`, previously Terminal-only) now covers the whole shell, root overlays and a shell-level portal root. Every legacy page and prompt renders in B palette and fonts and follows the B theme toggle; the mirror mode is retired.
- **Fixed on the way:** `--muted` self-reference (blanked muted text in the Terminal); `.dsb button` reset beat Tailwind utilities (stripped bg/colour from every reused A button, e.g. Connect Wallet) → `:where()`; the phone top bar grew to 120–140 px on short pages (grid rows) → `auto 1fr`.
- **Copy sub-tabs:** Watchlist, Dashboard, Portfolio and History get the B header and tabs (`CopyLayoutB`); phones in B get the full pages, not shell A's MobileCopy (which lacked Auto-copy, Live positions, Orders, Risk and Audit).
- **Prompts:** base `Modal` in B is a card, and a bottom sheet on phones. Body portals (market selector, mobile sheets) go to `#dsb-portal`. Toasts and the order confirmation sheet get the B root. Trader profile badges use B long/short/amber.
- **Charts:** they read B tokens inside shell B, and the page remounts on a theme toggle (except /trade).
- **Funding widget:** column overlap fixed (container-based grid); missing rate shows N/A, not NaN%.
- **Verification:** 19 routes + 4 prompts × 1440/390 × light/dark: 0 old-palette elements, only IBM Plex fonts, 0 px overflow (local build and prod). Shell A pixel-compare unchanged except live prices.
- **Deploy:** static overlay; old index at `/root/predeploy_20261002/index.html`.

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
