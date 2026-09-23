# Copy Trading — Changelog

## 2026-07-10 — Mobile Copy-Trading PWA (Discover / Watchlist / Dashboard + slide-in Profile + Set-up-copy sheet)

**What:** Built the dedicated mobile PWA experience for Copy Trading from the prototype
`perpl-copy-trading-pwa.html`, bound entirely to the existing v1 data layer (no new data
path, no backend change). Below the `md` (768px) breakpoint the `/copy/*` pages render the
new mobile UI; at/above it the desktop pages render unchanged.

**Files added:**
- `frontend/src/hooks/useIsMobile.ts` — `matchMedia(max-width:767px)` hook.
- `frontend/src/components/copy/mobile/marketLogos.tsx` — real brand SVG logos (BTC/ETH/SOL/MON/ZEC/HYPE) by symbol + neutral coin fallback (never a letter).
- `frontend/src/components/copy/mobile/mobile.css` — scoped `mc-` styles using the app's redesign tokens (both themes; prototype `--surface-3` → app `--surface-hover`).
- `frontend/src/components/copy/mobile/MobileCopy.tsx` — shell: screen head + LIVE badge (real `COPY_LIVE_ENABLED`), warning banner, segmented Discover/Watchlist/Dashboard, filter pills, trader cards, watchlist, dashboard aggregates, toast; updates `<meta name="theme-color">` on theme change.
- `frontend/src/components/copy/mobile/MobileTraderProfile.tsx` — full-screen slide-in profile (real `useTrader`: stats, equity curve, live positions w/ token logos), hardware-back + Esc support.
- `frontend/src/components/copy/mobile/MobileCopySheet.tsx` — Set-up-copy bottom sheet; same `CreateSubscriptionPayload` + validation as `StartPaperCopyModal`, saved via `useSubscriptions().create` (`POST /api/copy/subscriptions`).

**Files modified:**
- `frontend/src/pages/copy/Discover.tsx`, `Watchlist.tsx`, `CopyDashboard.tsx` — render `<MobileCopy>` when `useIsMobile()` (wrapper pattern; desktop path untouched).
- `frontend/src/components/layout/Header.tsx` — show the existing `ThemeToggle` on mobile too (`md:hidden`); mobile previously had no theme toggle.

**Data reuse (all real, nothing fabricated):** `useTraders` (`GET /api/traders`), `getEquityBatch` (`/api/traders/equity`), `useTrader` (`/api/traders/{w}` + `/stats` + `/positions` + `/equity`), `useWatchlist` (`/api/watchlist`), `getActiveSummaries` (`/api/traders/active-summary`), `useSubscriptions` (`/api/copy/subscriptions`), `useCopyPortfolio`, `useMarkets` (`/api/markets`), `useAuth`, `useTheme`, `lib/formatters`. Sparklines reuse `components/copy/Sparkline`.

**Honest gaps (per spec — listed, not faked):** no "daily budget used" endpoint exists → dashboard shows the configured `max_daily_loss` cap labelled "used n/a"; trader-profile marks are REST snapshots (no WS on profile, same as desktop); watched traders not in the current leaderboard page show a compact real row (name + active-position badge) until the profile loads full stats.

**Verified:** `vite build` clean (emits `MobileCopy` chunk); headless render at 390px in dark + light — layout correct, no horizontal overflow (docSW 380 < 390), all text wraps, segmented tabs/pills/warning/empty+error states render. Backend untouched, no execution/signing changes. **Deployed** to `the legacy host` (backup `/root/predeploy_copypwa_20260710_104047.tar.gz`).

### 2026-07-10 (fix) — Mobile scroll broken + overlays trapped behind footer

**Reported on device:** components didn't fit, content hidden behind the bottom nav, scrolling dead.

**Root causes:**
1. `.mc-root { overflow-x: hidden }` forced computed `overflow-y: auto`, turning the copy content into a nested scroll container with no height → traps/kills vertical scroll on iOS-Safari PWAs. → changed to `overflow-x: clip` (clips the watermark without creating a scroll container or forcing overflow-y).
2. The slide-in Profile, copy bottom-sheet, backdrop, and toast are `position: fixed` but rendered inside the app's `<main>` (a scrolling, `overflow-x-hidden`, `animate-pageIn`-transformed container). A fixed element inside a transformed/overflow ancestor is positioned/clipped by that ancestor, not the viewport → didn't fit, sat under the footer, wouldn't scroll. → `createPortal(..., document.body)` for MobileTraderProfile, MobileCopySheet, and the toast so they're true viewport overlays.

**Files:** `mobile.css` (overflow-x clip), `MobileTraderProfile.tsx`, `MobileCopySheet.tsx`, `MobileCopy.tsx` (portals). Verified with a local reverse-proxy (serves built dist + proxies `/api` to prod) → real leaderboard cards render at 390px (gold/silver/bronze badges, real PnL/ROI/volume, live sparklines). Redeployed (backup `/root/predeploy_copypwa2_20260710_110711.tar.gz`).

### 2026-07-10 (fix) — Trader card button alignment/visual

**Reported:** watch + Profile/Set-up-copy buttons on the cards not aligned / not proper.

**Cause:** actions were `<span role="button">` (to avoid `<button>` nested in the card's `<button>`); blockified as grid items they inherited `text-align:left`, so button labels rendered left-aligned. Watch star used `var(--faint)` → too low-contrast to read.

**Fix:** card is now a `<div role="button">` so Profile/Set-up-copy/watch are real `<button>`s; `.mc-btn` is `flex` center-aligned (`justify-content/align-items/text-align: center`); watch star bumped to 34px, `var(--dim)` icon + `--border-strong`. Verified centered/visible via local proxy render at 430px. Redeployed (backup `/root/predeploy_copypwa3_20260710_123141.tar.gz`).

## 2026-06-26 — Fix: TraderCard vs TraderProfile open-positions mismatch

**Root cause:** the cards read via `active_positions.py` (DYNAMIC market registry → has
HYPE id 40, no SOL), but `GET /api/traders/{wallet}/positions` (profile) used
`chain_reader.get_trader_positions_only`, which iterates the **hardcoded
`chain_reader.MARKETS`** (still BTC/MON/ETH/**SOL**, **no HYPE**). So a HYPE position
showed on the card but the profile read nothing.

**Fix (backend, unify source):** `GET /api/traders/{wallet}/positions` now calls
`active_positions.get_summary(wallet)` — the SAME cached, registry-based service the
cards use — and returns its `active_markets` as `positions` (+ `active_positions_count`/
`has_active_positions`/`active_positions_error`). Added `mark_price`/`pnl`/`deposit`/
`notional` to `active_positions` market objects so the profile table + Live Copy button
have every field. `chain_reader` is untouched (still used by trader_tracker/following feed).

**Frontend:** `useTrader` exposes `positionsError`; TraderProfile empty-states →
"Checking active trades…" (loading) / "No active trades" (count 0) / "Open trades
unavailable" (error); heading badge matches. Copy button gets full fields (incl. mark_price).

**Verify:** card vs profile MATCH for the previously-broken trader (HYPE long, count=1,
copy-fields ok), HYPE present, SOL absent. Read-only; no env/migration; terminal +
order code + chain_reader unchanged; live_auto 400. Deployed to prod.

## 2026-06-26 — Active trader positions count on Discover/Watchlist/Profile cards

**Why:** users had to open every trader profile to see if a trader has open trades to copy.

- **Backend `services/active_positions.py`** (new): per-wallet LIVE on-chain position
  summary (count + per-market side/size/leverage/entry/unrealized PnL). Reads via a
  dedicated tuned web3 pool (pool+retries, concurrency 6) using the **dynamic market
  registry** for the market list + decimals + symbols (NOT hardcoded `chain_reader.MARKETS`)
  — so HYPE is detected and delisted SOL is never probed. Per-wallet cache (45s success /
  8s error), per-wallet timeout, fully error-safe (failure → `active_positions_count: null`,
  `active_positions_error: true`). Cache-only `get_cached` + fire-and-forget `prime`.
- **`routers/traders.py`:** `GET /api/traders` now attaches `active_positions_count` /
  `active_markets` / `has_active_positions` — **cache-only + background prime** so the page
  never blocks (uncached → `active_positions_pending`, fills on the next poll). New batch
  `GET /api/traders/active-summary?wallets=` (awaited; for watchlist).
- **Frontend:** `TraderCard` badge ("N active trades") + per-market chips
  ("HYPE Long"/"BTC Short") + highlight; states for 0 / pending / unavailable. `useTraders`
  re-polls (capped) while pending. Discover "Open trades only" toggle (limit 24). Watchlist
  rows show counts via the batch endpoint. TraderProfile heading shows "N active trades".

**Perf:** `/api/traders` instant (0.000s, cache-only); counts fill ~6/5s in background,
~24 traders within ~40s, then cached. No paper/copy tables queried; leader live positions only.

**Tests:** service against live chain (HYPE long / BTC short / MON+HYPE detected, 8/8 with
retries); `/api/traders` shape + instant pending; batch returns real counts; SOL never
appears. `vite build` + `py_compile` clean. Terminal OrderForm/PositionsPanel unchanged;
no order-placement code touched; live_auto untouched. NOT deployed yet.

## 2026-06-26 — Live copied position tracking + manual close/reduce flow

**Goal:** track real copied positions after a live copy order fills, and let the
follower confirm a close/reduce (client wallet-signed) when the leader closes/reduces.
Backend never places orders — it only tracks.

- **Model:** new `copy_live_positions` (lifecycle open/partially_closed/closed/failed/
  orphaned + entry/current size, margin, leverage, realized_pnl, close-suggestion
  fields). Added `action` (open|close|reduce) + soft `live_position_id` to `copy_orders`.
  Idempotent migration (create_all builds the table).
- **Service `services/copy/live_positions.py`:** `open_from_order` (idempotent on
  copy_order_id — duplicate fill = no dup), `apply_close_result` (close→closed,
  reduce→partially_closed, realized PnL math), `handle_leader_event` (leader
  closed/reduced → set `close_suggested` on matching open positions; NEVER an order),
  `has_pending_close`, `list_positions`, `get_position` (owner-scoped).
- **`live_orders.create_attempt`:** now takes `action`+`live_position_id`. Close/reduce
  gated by live+mode+allowlist+Perpl-eligibility + owner + position-open + no-duplicate
  (skip leverage/margin checks since closing reduces risk).
- **`routers/copy.py`:** POST `/orders` accepts action/live_position_id; PATCH `/orders/{id}`
  on filled → open/close/reduce the live position + audit (`live_position_opened` /
  `live_position_closed` / `live_position_reduced`); on failed → `live_close_failed`
  (position stays open). New `GET /api/copy/live-positions`. `trader_tracker` calls
  `live_positions.handle_leader_event` after recording each leader event.
- **Frontend:** `useCopyTrade.closeLivePosition` (reuses `perplTrading.closePosition`,
  copyPosition untouched), `LiveCloseModal` (real close/reduce warning, % slider,
  config-missing guard), `LivePositionsTable` (status, PnL incl. live unrealized,
  "leader closed/reduced" badge, Close/Confirm button), dashboard **Live Positions** tab
  + "Open Live Positions" summary. `copyApi`/`useCopyV1` additions.

**Tests:** lifecycle 9/9 (filled→position, dup fill no dup, leader close→suggestion,
owner close→closed+PnL 9.9, failed close stays open, **non-owner blocked
position_not_found**, audit trail). `vite build` + `py_compile` clean. Terminal OrderForm
+ PositionsPanel unchanged; no backend order-placement path; `live_auto` 400.
**NOT deployed yet** (prod has live copy ON but without position tracking).

## 2026-06-26 — Live-copy gate corrected: Perpl approval, not custom allowlist

**Why:** Perpl already controls who may trade. The app is a tool on top of Perpl, so any
Perpl-approved wallet should be able to live-copy when `COPY_LIVE_ENABLED=true`. The custom
wallet allowlist is demoted to an optional private-beta override.

- `config.py`: added `COPY_REQUIRE_INTERNAL_ALLOWLIST=false` (default). `COPY_LIVE_ALLOWED_WALLETS`
  now only checked when that is true. New helpers `is_live_copy_enabled()` +
  `internal_allowlist_ok(wallet)` (replaces `is_live_copy_wallet_allowed` /
  `is_live_manual_enabled_for_wallet`).
- `services/chain_reader.py`: `get_perpl_account_id()` / `is_perpl_eligible()` — on-chain
  `getAccountByAddr` (non-zero account id = Perpl-approved; the same signal the terminal uses).
  Contract reverts for no-account wallets → treated as not eligible (returns 0); RPC errors
  propagate so the gate can fail-closed.
- `services/copy/live_orders.py`: gate is now live_disabled → live_mode_disabled →
  *(optional)* wallet_not_allowlisted → **perpl_access_required** → risk → submitted.
  `_perpl_eligible()` runs the web3 call in a thread, fail-closed.
- Frontend `copyApi.copyOrderReasonLabel`: `perpl_access_required` →
  "Perpl trading access required"; `wallet_not_allowlisted` → "Live copy beta access required"
  (beta only). Default message is no longer the beta allowlist one.

**Behavior:** `COPY_LIVE_ENABLED=true` + `COPY_REQUIRE_INTERNAL_ALLOWLIST=false` → no internal
allowlist check; Perpl-approved wallet reaches `submitted`, non-approved → `perpl_access_required`.
`true` allowlist mode enforces `COPY_LIVE_ALLOWED_WALLETS`. `live_auto` still 400. Terminal
manual trading unchanged. Markets unchanged (HYPE present, SOL absent).

**Tests:** perpl-gate 7/7 (incl. allowlist-off ignores list, not-approved→perpl_access_required,
beta mode enforces allowlist); real on-chain check (live trader account_id=877 eligible, fake
wallet not). `vite build` + `py_compile` clean. No wallet address required for normal mode.

## 2026-06-25 — Live copy beta allowlist + dynamic Perpl market registry

**Part A — backend-enforced live copy allowlist.**
- `config.py`: `COPY_LIVE_ALLOWED_WALLETS` (csv→list, trimmed/lowercased, `NoDecode`)
  + helpers `is_live_copy_wallet_allowed()` / `is_live_manual_enabled_for_wallet()`.
- `services/copy/live_orders.create_attempt` gate order: live_disabled → live_mode_disabled
  → wallet_not_allowlisted → risk checks → submitted. Each block writes `copy_orders`
  (status `risk_blocked`), a `risk_events` row (`event_type` per reason, message
  "Live copy beta access required" for allowlist), and an `audit_logs` `live_copy_blocked`.
  `confirm_live_copy` audit now only on `submitted`. `live_auto` still 400.
- Frontend: `copyOrderReasonLabel()` maps `wallet_not_allowlisted` → "Live copy beta access
  required"; used in `LiveCopyModal` (block message) + `LiveOrdersTable` (note column).

**Part B — dynamic market registry (no hardcoded markets).**
- New `services/market_registry.py` (cache TTL 60s from Perpl context; `get_active_markets`/
  `get_market_by_id`/`get_market_by_symbol`/`refresh_markets`/`validate_market_ids`). Symbol
  read from context `name` ("" symbol for BTC/MON).
- `routers/markets.py`: `GET /api/markets` now returns envelope `{markets, source, fetched_at,
  ttl_seconds}` (superset: live price fields + decimals/is_active); added
  `GET /markets/symbol/{symbol}`, `POST /markets/refresh`. Frontend `getMarkets()` unwraps `.markets`.
- Copy validation now registry-driven: `copy.py _validate_markets` rejects delisted/unknown
  `allowed_markets` (400 on subscribe/update); `live_orders`/`risk` block delisted markets with
  `market_inactive` (tracked, not a hard 400 on the order path). Removed `VALID_MARKETS={1,10,20,30}`.
- Frontend: `lib/marketsApi.ts` + `hooks/useMarkets.ts` (TanStack Query). Copy-setup
  market chips from `useMarkets()` (active only, loading/error, submit gated, no hardcoded
  fallback). `LiveCopyModal` blocks ("Market config unavailable") when `MARKET_CONFIGS`
  missing — never defaults unknown decimals to BTC. Terminal selectors (`Trade.tsx`,
  `Sidebar.tsx`) iterate dynamic store ids; generic logo fallback for new symbols (HYPE).
  `constants.ts MARKETS` updated (SOL removed, HYPE id 40 added) and demoted to display fallback.

**Observed live:** Perpl now lists BTC/MON/ETH/HYPE — **SOL delisted, HYPE (id 40) added**.
Registry reflects this automatically.

**Tests:** allowlist 10/10, markets-http 8/8 (incl. SOL allowed_markets→400, HYPE→200,
symbol/SOL→404), market_inactive block, live-http regression 8/8. `vite build` clean,
`py_compile` clean. **Not deployed; `COPY_LIVE_ENABLED` stays false.**

## 2026-06-25 — Product direction corrected to LIVE-first (live_manual primary)

**Why:** Paper-copy was wrongly the primary product. Goal is live Perpl social copy
trading. Paper kept only as an optional sandbox. Live order placement reuses the
proven client path (useCopyTrade.copyPosition -> perplTrading), backend only TRACKS.

**Config:** `COPY_LIVE_ENABLED` is env-controlled (default false; gates real ORDER
PLACEMENT only). Added `COPY_MODE` (live_manual|live_auto|paper, default live_manual)
backend + frontend (`VITE_COPY_LIVE_ENABLED`, `VITE_COPY_MODE`). live_auto refused.

**Backend:** `copy_orders` extended with leverage, allocation_usd, perpl_request_id,
perpl_order_id, perpl_fill_id, fill_price, fill_size, submitted_at, filled_at,
error_message; statuses pending_confirmation|submitted|filled|failed|skipped|
risk_blocked|simulated. `mode` widened to VARCHAR(12) on copy_orders AND
copy_subscriptions (live_manual=11 chars was truncating). Idempotent migration in
main.py lifespan. Subscriptions no longer forced paper (`_resolve_mode`,
default=COPY_MODE). New `services/copy/live_orders.py` + endpoints
`POST /api/copy/orders` (record confirmed attempt + risk/live gate; off ->
risk_blocked) and `PATCH /api/copy/orders/{id}` (placement result). GET /orders gains
`mode` filter. Every attempt -> copy_orders; every block -> risk_event; confirm +
result -> audit_logs.

**Frontend:** new `LiveCopyModal` (per-trade confirm -> copyPosition real order ->
PATCH result) on Trader Profile positions. Removed paper-only banners; CopyLayout is
live-first ("Live places real Perpl orders" warning, LIVE/LIVE-OFF chip). "Start Paper
Copy" -> "Set up Copy"/"Copy"; subscription modal saves live_manual risk settings.
Dashboard live-first: Subscriptions / Live Copy Orders / Risk Events / Audit + a
secondary "Paper Sandbox" tab. New `LiveOrdersTable`.

**Safety/verify:** real order ONLY in LiveCopyModal (client) after user confirm AND
status=='submitted' AND COPY_LIVE_ENABLED. Terminal OrderForm unchanged.
_trigger_auto_copies never called (no auto execution); trader_tracker runs only paper
sims. Tests: live_orders 9/9, live HTTP 8/8 (gate off->risk_blocked, on->submitted->
filled, idempotent, sub limits, live_auto->400, audit). vite build + py_compile pass.
NOT deployed (kept local; COPY_LIVE_ENABLED stays false).

## 2026-06-25 — Phase D.5 QA + v1 deployed to server

**Why:** Verify the Phase D frontend works e2e with the B/C backend, then ship A–D
to production as v1.

**QA (all green):** DB constraints (max_total_loss / copy_paper_positions /
unique_event_key / idempotency_key); route ranking safe; **17/17** API contract +
in-process HTTP runtime (ASGI + minted JWT); **11/11** paper-engine smoke
(open→order+position, duplicate→no dup, blocked market→risk_event, close→closed);
safety greps clean (no useCopyTrade/perplTrading/placeOrder/copyPosition in v1 UI);
`vite build` + `py_compile` pass. Scripts: scratchpad `qa_paper_engine.py`, `qa_http.py`.

**Deploy (<server> / the legacy host):** server is not a git repo →
tar+scp `backend/app`; restart `perpl-terminal` → create_all built all 9 v1 tables;
local vite build → scp dist → reload nginx. Backup `/root/predeploy_v1_20260625_135458.tar.gz`.
Verified live: `/api/traders` real data, `/api/copy/*`+`/api/watchlist` 422 w/o token,
`/copy/discover` SPA 200. Pushed `a48d3f2` to GitHub master.

## 2026-06-25 — Copy Trading v1 Phase D (frontend rebuild, paper-only)

**Why:** Backend v1 (Phases A–C) exposed new paper-only copy endpoints
(`/api/traders*`, `/api/watchlist*`, `/api/copy/*`). The old `/copy` UI still used
the legacy v0 flows (useCopyTrade copy path, auto-copy language). Phase D rebuilds
the copy frontend on the v1 endpoints. No live execution; `COPY_LIVE_ENABLED` stays
false; terminal/manual trading untouched.

**New API layer**
- `frontend/src/lib/copyApi.ts` — typed wrappers for all v1 endpoints (traders,
  watchlist, subscriptions, orders, positions, risk-events, audit-logs). Never
  imports `perplTrading`; never places real orders.
- `frontend/src/hooks/useCopyV1.ts` — data hooks: `useTraders`, `useTrader`,
  `useWatchlist(enabled)`, `useSubscriptions(enabled)`, `useCopyOrders`,
  `useCopyPositions`, `useRiskEvents`, `useAuditLogs`. Do NOT call `useCopyTrade`.

**New components (`frontend/src/components/copy/`)**
- `TraderCard.tsx` — discovery card (PnL/ROI/Volume) + View Profile / Watch /
  Start Paper Copy.
- `StartPaperCopyModal.tsx` — validated subscription form (allocation>0,
  leverage 1–20, margin/daily/total loss>0, slippage 0–500 bps, market multi-select,
  fixed sizing only, copy_new_only default true). Submits `POST /api/copy/subscriptions`.
- `PaperCopySubscriptionCard.tsx` — subscription summary + pause/resume/stop.
- `PaperPositionsTable.tsx`, `PaperOrdersTable.tsx` — simulated positions / decision log.
- `RiskEventsPanel.tsx`, `AuditLogPanel.tsx` — risk + audit feeds.
- `PaperOnlyBanner.tsx`, `CopyLayout.tsx` (sub-nav + "Live copy coming soon"),
  `ConnectPrompt.tsx`.

**New pages (`frontend/src/pages/copy/`)**
- `Discover.tsx` (`/copy/discover`), `TraderProfile.tsx` (`/copy/trader/:wallet`),
  `Watchlist.tsx` (`/copy/watchlist`), `CopyDashboard.tsx` (`/copy/dashboard`).

**Routing (`frontend/src/App.tsx`)**
- `/copy` now redirects to `/copy/discover`; added the four v1 routes as lazy chunks.
- Removed the eager `CopyTradePage` import from App (legacy `pages/CopyTrade.tsx`
  and `components/copytrade/*` kept on disk, no longer referenced by the v1 UI).
- Legacy `/copy/:leaderId` → `LeaderDetailPage` route kept for backwards compat.

**Verification**
- `npx vite build` passes; new chunks emitted (CopyLayout, StartPaperCopyModal,
  TraderProfile, CopyDashboard, Discover, Watchlist).
- grep across `components/copy/`, `pages/copy/`, `copyApi.ts`, `useCopyV1.ts`:
  no `useCopyTrade`, no `perplTrading`, no `placeOrder` (only self-documenting comments).
- Terminal `OrderForm.tsx` unchanged — manual path still `useCopyTrade().copyPosition`.
- `COPY_LIVE_ENABLED = false` (frontend constants + backend config) unchanged.

---

## 2026-07-13 — Fix: Telegram leader-trade alerts dead since v1 (follower lookup + tracker poll set rewired to v1 tables)

**Files modified**
- `backend/app/services/telegram_bot.py`
- `backend/app/services/trader_tracker.py`

**Why**
Telegram entry/exit alerts to followers silently stopped working. Two causes:
1. `_get_followers_with_telegram` only joined legacy `wallet_follows`, but the
   current v1 UI writes follows to `watchlists` / `copy_subscriptions` — so
   followers added via Discover/Watchlist could never receive alerts.
2. `trader_tracker._tick` only polled top-10 leaderboard + `wallet_follows`
   leaders, so a v1-watched trader outside the top 10 was never even detected.
Additionally `telegram_links` is empty in prod (wiped by the fresh v1 DB rebuild
on 2026-06-25) — users must re-link Telegram via Settings before any alert can
be delivered. That part is a user action, not a code fix.

**What changed**
- `telegram_bot._get_followers_with_telegram`: follower set is now the union of
  v1 `watchlists` (trader_wallet match) + active `copy_subscriptions` + legacy
  `wallet_follows` (kept for 45 pre-v1 rows); chat_ids then resolved via
  `telegram_links` join as before. No change to message content or send path.
- `trader_tracker._tick`: poll set now also includes distinct `watchlists.trader_wallet`
  and active `copy_subscriptions.trader_wallet` (error-safe, same pattern as the
  existing WalletFollow block).

**Deploy**
- scp 2 files → <server> `/var/www/terminal/backend/app/services/`,
  `systemctl restart perpl-terminal`. Backup `/root/predeploy_tgfix_20260713_105547.tar.gz`.
- Verified: startup clean ("Telegram bot started"), /api/markets 200, bot token
  valid (getMe → @tradewithquant_bot), no tracker errors after restart.
- In-proc prod smoke: follower union finds 2 wallets for watched trader
  `0x2126…aa6e`; chat_ids [] as expected (telegram_links empty until users re-link).

---

## 2026-07-13 — UX: show "slow down" toast on 429 for watch/unwatch (was silent failure)

**Files modified**
- `frontend/src/hooks/useCopyV1.ts`

**Why**
When the rate limit was exceeded, Watch clicks failed silently — the promise
rejection was uncaught, no user-facing message, button state just didn't change.

**What changed**
- `errMsg()` special-cases HTTP 429: reads the `Retry-After` response header and
  returns "Too many requests — please slow down. Try again in ~Ns." (also flows
  into every hook's `error` state, e.g. subscription actions).
- `useWatchlist.watch/unwatch` now catch failures and show a global toast
  (warning style for 429, error style with reason otherwise) instead of
  throwing uncaught. Covers Discover, Watchlist, and profile modal — all use
  this hook. ToastContainer is already mounted globally in App.tsx.

**Deploy**
- Frontend-only: vite build → dist tarball → prod, nginx reloaded, backend NOT
  restarted. Backup `/root/predeploy_429toast_*.tar.gz`. Verified: /copy routes
  200, deployed CopyLayout chunk contains the new message string.

---

## 2026-07-13 — Fix: watch/unwatch click storm + missing slow-down toast on rapid clicks

**Files modified**
- `frontend/src/hooks/useCopyV1.ts`

**Why**
Nginx log showed the SAME watchlist DELETE firing 5-6x/second for 6+ seconds —
the toggle had no in-flight guard, so every rapid click fired a request (and a
failed unwatch leaves the trader "watched", so each click re-fires DELETE).
That storm burned the 30-writes/min budget instantly. Separately, the user's
open tab was still running the pre-toast bundle, so no message appeared.

**What changed**
- `useWatchlist.watch/unwatch`: per-wallet in-flight guard (`pendingRef` Set) —
  clicks while a request for that wallet is pending are ignored.
- New `toastActionError()` helper: 429 shows the "slow down, try again in ~Ns"
  warning toast at most once per 5s (no toast storm); other errors show reason.

**Deploy**
- Frontend-only: vite build → dist → prod, nginx reloaded. New bundle
  `index-4E6yaNJ-.js`, chunk `CopyLayout-GIw6FKdY.js` (verified contains guard +
  message). Users with an open tab/PWA must refresh to get it.

---

## 2026-07-13 — Telegram alert redesign (cleaner layout, inline buttons, no link-preview card)

**Files modified**
- `backend/app/services/telegram_bot.py`

**Why**
Alerts looked cluttered: plain text wall, raw "Open Terminal" link that made
Telegram attach a huge website preview card under every alert, and large sizes
rendered in scientific notation (65154 → "6.515e+04" via `:,.4g`).

**What changed**
- `send_notification` now supports inline keyboard buttons and always disables
  link previews (`LinkPreviewOptions(is_disabled=True)`).
- Entry: "🟢/🔴 LONG BTC — position opened" headline + 👤 trader, values in
  <code> blocks. Exit: ✅/❌ by PnL sign, entry→exit prices, PnL bold.
  Copy-queued restyled the same way.
- Both alerts get buttons: 📊 Open Terminal + 👤 Trader Profile (deep link to
  /copy/trader/{wallet}).
- New `_fmt_qty()` — sizes ≥1000 formatted as "65,154", no sci-notation.
- No change to follower resolution, send path, or any trading logic.

**Deploy/verify**
- scp + restart perpl-terminal (bot restarted clean). Backup
  `/root/predeploy_tgdesign_telegram_bot.py`. Sample alert in the exact new
  format sent to the linked user via bot API using a REAL live position
  (watched trader 0xf170…1c49 long MON) — delivered OK (message_id 2940).

---

## 2026-07-13 — Telegram alerts: tap-to-copy wallet address

**Files modified**
- `backend/app/services/telegram_bot.py`

**Why / what changed**
Trader identity line now shows the FULL wallet address wrapped in <code>
(Telegram makes code text tap-to-copy; the old shortened 0xab…cd form would
have copied only the shortened text). Username, when set, stays bold above the
address. Applied via new `_trader_line()` in entry/exit/copy-queued alerts.

**Deploy**
- scp + restart perpl-terminal, bot restarted clean. Preview sent to linked
  user (message_id 2947).

---

## 2026-07-13 — Fix: phantom exit/entry alerts from failed RPC reads + honest prices in entry alerts

**Files modified**
- `backend/app/services/trader_tracker.py`
- `backend/app/services/telegram_bot.py`

**Why**
User received "LONG ETH @ $1,785.89" alert at 16:17 IST while ETH traded at
~$1,776-1,778. Log forensics: the trader's ETH long was detected "closed" at
12:47:22 and "reopened" 37s later at the IDENTICAL avg entry — plus his BTC
fired "new entry @ 62045.4" on 4 occasions across 3 days. Root cause:
`_read_positions` swallowed per-market RPC errors (`except: pass`) so the
position vanished from one snapshot → fake exit (with fake PnL alert) → next
poll fake entry showing the old on-chain avg entry price. Alert delivery
itself was instant (sent 1s after detection).

**What changed**
- `_get_account_id` returns -1 on RPC failure (was 0 = indistinguishable from
  "no Perpl account" → all positions looked closed).
- `_read_positions` returns (positions, failed_market_ids); account-read
  failure returns None.
- `_tick` diff: None → skip wallet, keep old snapshot; failed markets carry
  forward their previous snapshot state (a failed read is not a close); first
  sighting with any failed market skips seeding (no incomplete baseline).
- Entry alert now shows BOTH "Avg entry" (on-chain average — relabeled from
  "Entry price") and "Price now" (live mark from ws_manager at alert time).

**Deploy/verify**
- Backup /root/predeploy_phantomfix_*.tar.gz, scp 2 files, restart. Tracker
  running clean post-restart (real entries/exits detected, 0 errors). Preview
  with real live data (0xf170 MON long, mark 0.022153) sent to linked user.

---

## 2026-07-14 — Audit fixes batch 1+2 (security + trading execution)

**Backend** (`auth.py`, `copy.py`, `copytrade.py`, `main.py`, `perpl_client.py`)
- C1: `/auth/perpl-connect` now auth-gated (JWT), rejects address≠JWT wallet (403), and stores the Perpl cookie jar ENCRYPTED (`_encode/_decode_perpl_session`, legacy-plaintext read fallback).
- C2: copy close/reduce PATCH now requires real `perpl_order_id`/`perpl_fill_id` before mutating a live position (same gate as opens); unverified → order failed, position untouched, audited.
- C3: `/copytrade/leaders/{w}/followers` now requires auth (was public follower-graph leak).
- C4: SIWE nonce single-use — `/auth/payload` records issued nonce (10min TTL), `/auth/connect` consumes it; replayed/unknown nonce → 401.
- C5: startup refuses to boot on default/empty `JWT_SECRET`.
- B2: Perpl REST client `_get_json` with 429-aware exponential backoff (honors Retry-After); context+leaderboard use it.
- B5: `/api/candles` proxy validates resolution against the official set and caps at 1024 candles; 429 surfaced as 429 not 500.

**Frontend** (`perplTrading.ts`)
- A1: `placeSlTpOrder` no longer sends the malformed `t:5`/`t:6` order (spec: Cancel / IncreasePositionCollateral) — throws a clear "not available yet"; callers already catch. Removes the unintended on-chain collateral action.
- A2: `placeOrder`/`closePosition` resend the SAME `rq` once on timeout (Perpl at-most-once → no duplicate), then give up with a check-your-positions warning.
- A3: `closePosition` rejects on a rejected close (no more false "Closed" + fake history) and surfaces the real Perpl order id.
- A4: order outcome classified via spec `st` codes first, then legacy fs/r; full `sr` reject-reason map.

**Deploy/verify** — backend scp+restart (JWT guard passed, /api/markets 200); prod checks: followers+perpl-connect now blocked (422), candle bad-resolution/oversized 400, valid 200. Frontend vite build → dist, bundle `index-CEyVWfzD.js` (contains new retry + SL/TP-disabled logic). Backups `/root/predeploy_auditfix_*`.
⚠️ Copy-close gate + trading changes need a funded-wallet click-through before relying on them.

---

## 2026-07-14 — Audit fixes batch 3 (market maps B1, WS resilience B3, log hygiene C7)

**B1 — market maps registry-driven** (found: SOL is re-listed at id **31**, HYPE=40, ZEC=50; hardcoded {1,10,20,30} was wrong on every count)
- `chain_reader.MARKETS` seeded to current reality and refreshed IN PLACE via new `apply_registry()`; `market_symbol()` helper added.
- `market_registry.refresh_markets` + `ws_manager` poll loop push live markets into chain_reader every cycle (no extra HTTP). ws_manager now processes ALL live markets, not `[1,10,20,30]`.
- `orders.py`/`trades.py` symbols, `sl_tp.py` market validation, `perpl_report.py` decimals now source from chain_reader (live). Frontend `constants.ts` fallback map corrected (SOL=31, HYPE=40, ZEC=50).
- Verified on prod: `chain_reader.MARKETS = {1:BTC,10:MON,20:ETH,31:SOL,40:HYPE,50:ZEC}`.

**B3 — market-data WS resilience** (`useMarketDataWs.ts`): app-level ping (mt:1) every 30s, heartbeat `sn` sequence-gap detection → resubscribe, and auto-reconnect with exponential backoff (1s→15s). Was: no ping, sn discarded, empty onclose/onerror.

**C7 — log hygiene** (`trading_proxy.py`): full WS order payloads no longer logged at info (now debug byte-count only).

**Not auto-fixed (documented decisions):** A5 (triggered SL/TP doesn't place a close — backend has no order path; SL/TP stays tracking-only, and the malformed exchange send is now disabled per A1); C6 (`/terminal-stats` left public — aggregate stats + already-public leader wallets).

**Deploy** — backend scp+restart, frontend vite build→dist. Backups `/root/predeploy_b1_*`, `/root/predeploy_b3c7_*`. /api/markets 200, /trade + /copy 200.

## 2026-07-17 — Trade open time + live "active for" watch on trader profile

**Files modified:**
- `backend/app/services/active_positions.py` — read `entryBlock` (index 7 of the on-chain PositionInfo struct, previously ignored) and resolve it to a unix timestamp via `w3.eth.get_block()` with a module-level block→ts cache (block timestamps are immutable). New `opened_at` field (unix seconds, `null` if lookup fails) on every position in `/api/traders/{wallet}/positions` and the Discover/Watchlist active-summary.
- `frontend/src/lib/copyApi.ts` — `opened_at?: number | null` added to `TraderOnChainPosition`.
- `frontend/src/components/copy/OpenDuration.tsx` — NEW: `formatOpenedDate()` (e.g. "17 Jul, 09:27") + `<OpenDuration>` live watch in DD:HH:MM format, re-renders every 30s, full datetime on hover.
- `frontend/src/pages/copy/TraderProfile.tsx` — two new columns in Live On-Chain Positions table: "Opened" (date+time) and "Active" (ticking DD:HH:MM).
- `frontend/src/components/copy/mobile/MobileTraderProfile.tsx` + `mobile.css` — same two cells in the mobile position card; grid 4→3 cols (2 rows of 3).

**Why:** user asked to show when each trade was opened / how long it has been active on the trader profile.

**Note:** `entryBlock` is what the contract stores — if the trader adds to a position the contract may update it; it is the only open-time source on-chain.

**Deploy:** backend scp + `systemctl restart perpl-terminal`, frontend vite build → dist → nginx reload. Backups: `/root/active_positions_pre_openedat_*.py.bak`, `/root/predeploy_openedat_*.tar.gz`. Verified live: `/api/traders/0xa262…/positions` returns `opened_at` (BTC short opened 2026-07-17 09:27 UTC).

**Build note:** `npm run build` (`tsc -b`) fails with pre-existing TS6305/TS6310 tsconfig issues — deploy build done with `npx vite build` directly (same dist output).

## 2026-07-17 (follow-up) — Opened/Active columns missing from Discover/Watchlist profile popup

**Files modified:** `frontend/src/components/copy/TraderProfileModal.tsx`, `frontend/public/sw.js`

**Why:** user still couldn't see the trade-open time/watch. Root cause: clicking a trader on Discover/Watchlist opens `TraderProfileModal` — a separate component from the `pages/copy/TraderProfile.tsx` page updated earlier; the modal was never touched.

**What changed:** same "Opened" (date+time) + "Active" (live DD:HH:MM `<OpenDuration>`) columns added to the modal's positions table. SW cache bumped `perpl-v6` → `perpl-v7` to force clients to drop the stale bundle. Rebuilt (`npx vite build`) + deployed, verified `"Opened","Active"` present in deployed `TraderProfileModal-*.js` and `perpl-v7` in deployed `sw.js`.

## 2026-07-17 (fix 2) — opened_at showed wrong times + watch didn't tick

**Files modified:** `backend/app/services/active_positions.py`, `frontend/src/components/copy/OpenDuration.tsx`, `frontend/public/sw.js`

**Why (verified live, not assumed):** on-chain `PositionInfo.entryBlock` is NOT the open time — the contract updates it whenever the position is touched. Proven on prod: the same open BTC short's entryBlock resolved to 09:27 UTC at 12:07, then to 12:12 a few minutes later. So durations showed minutes for positions open for days. Second complaint: watch ticked every 30s with only minutes displayed → no visible movement.

**What changed:**
- Backend: dropped the entryBlock→timestamp path entirely. `opened_at` now comes from the trader-tracker's real recorded data (`trader_activity` table, 30s polling of top leaderboard wallets, 11K+ rows since 2026-03-27): latest `entry` for (wallet, market) with no later `exit`, side must match, else `None` (frontend shows "—" instead of a wrong number). Enrichment runs in `get_summary()` after each fresh compute (`_enrich_opened_at`).
- Frontend: `OpenDuration` now ticks every 1s and shows `DD:HH:MM:SS` so the seconds visibly move.
- SW cache `perpl-v7` → `perpl-v8`.

**Verified:** API `opened_at` values now exactly equal tracker entry timestamps (BTC short 09:55:31 = tracker row; MON short 2026-07-15 21:14 ≈ 1.7 days — was showing minutes before). Deployed bundle contains new modal chunk + `perpl-v8`.

**Limitation:** wallets never in the tracked top-10 set, or positions opened before tracking began, show "—" (no fake numbers).

## 2026-07-28 — Max leverage fetched dynamically from Perpl (was hardcoded/stale)

**File:** `backend/app/main.py` (`_get_real_max_leverage`, `/api/market-configs`)

**Why:** Max leverage per market was a hardcoded map `{1:10,10:5,20:10,30:20}`. It went stale when Perpl changed initial margins and SOL re-listed at id 31 → BTC showed 10x (really 6x), MON 5x (really 10x), ETH 10x (really 8x). Affects the order-form leverage slider and copy-engine leverage clamp.

**What changed:** removed the hardcoded map; max leverage is now `floor(10000 / config.initial_margin)` from Perpl's live context (initial_margin is in 1/10000 units; floor keeps the integer slider within the required margin). Live: BTC 6x, MON 10x, ETH 8x, SOL 8x, HYPE 10x, ZEC 12x. Backend deploy (`systemctl restart perpl-terminal`), commit `09ac44f`, backup `/root/main_py_backup_*.py`.

## 2026-07-28 (correction) — Max leverage formula was INVERTED

**File:** `backend/app/main.py` (`_get_real_max_leverage`)

**Why:** The earlier "dynamic" fix used `10000 / initial_margin`, which was inverted — it gave BTC 6x / ZEC 12x (blue-chip LOWER than an altcoin, backwards) and was wrong for all but the two markets where initial_margin happened to equal 1000. User flagged all values wrong.

**Correct formula (per Perpl margin docs):** IMF (initial margin fraction) IS the max leverage, and the config `initial_margin` field carries it in HUNDREDTHS (same encoding as the order `lv` field, 1000 = 10.00x). So **max_leverage = initial_margin / 100**. This reproduces the old hardcoded caps {BTC:10,MON:5,ETH:10,SOL:20} under the margins Perpl used then (1000/500/1000/2000) — the inverted formula never could. Live: BTC 15x, MON 10x, ETH 12x, SOL 12x, HYPE 10x, ZEC 8x. Also confirmed Perpl's API has NO explicit max_leverage field (full REST context + WS market-config dump) — initial_margin is the only source. Commit `9eb701d`.
