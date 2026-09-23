# Mobile / PWA / App-shell Changelog

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
