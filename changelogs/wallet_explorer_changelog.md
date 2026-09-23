# wallet_explorer module changelog (Explorer pages, shared HL client, Envio indexer)

## 2026-09-07 — Part 1: shared HL info client with weight budget + priority classes
**Files**: `backend/app/services/hyperliquid/client.py` (NEW), `backend/app/services/hyperliquid/{profile,tracker,fill_stats,prices,leaderboard}.py`, `backend/app/services/analytics/{hedger,position_sweep}.py`, `backend/app/services/hyperliquid_client.py`, `backend/app/main.py` (/health metrics + lifespan aclose), `backend/app/config.py` (`HL_WEIGHT_CEILING=1000`), `backend/tests/test_hl_client.py` (NEW, 5/5), `backend/app/utils/rate_limiter.py` (REMOVED — dead since birth).
**Why**: Wallet Explorer build order Part 1 — every HL info call must share one budget-aware client before the explorer endpoints (lowest priority, shed first) can exist; previously five modules each owned an httpx client with no weight accounting and `Retry-After` was never read.
**What**: `post_info()` with per-request weight (2/20/60 base + measured per-item extras: ceil/20, candleSnapshot ceil/60), rolling 60s window vs soft ceiling 1000 of 1200, priorities critical (copy/live, borrows headroom, never shed) / background (waits) / explorer (shed after 2s, needs 150 free headroom), per-class circuit breaker honoring Retry-After with exponential default (429 responses passed through so all existing module 429 discipline still runs), metrics on GET /health (`hl_weight`) + weight/min log line. Leaderboard file host streams through the pool weight-free. All migrations behavior-preserving (budgets, pacing, caches, `_fg_active`, resume queues untouched). Deployed with MM gate (`running: 0`), prev tree `/root/prev_trees/explorer_p1_20260907_182224/`.

## 2026-09-07 — Part 2: HL wallet explorer page (/wallet/:address, shell B)
**Files**: `backend/app/routers/explorer.py` (NEW — /api/explorer/hl/{address} tier-1 300s + fills/ledger/funding/orders/extras auth-gated tabs, all EXPLORER priority, measured weight_cost per payload, HIP-3 = "canonical dex only"), `backend/app/main.py` (migration v18 explorer_address_meta + router mount), `backend/app/services/hyperliquid/profile.py` (additive: roe/funding_since_open/margin_mode per position, withdrawable/margins on state, portfolio full parse w/ accountValueHistory + perp-only periods, priority kwarg), `frontend/src/lib/explorerApi.ts` (NEW), `frontend/src/design-b/screens/WalletExplorerB.tsx` (NEW), `frontend/src/design-b/ExplorerSearch.tsx` (NEW), `frontend/src/design-b/ShellB.tsx` (Explorer nav under Markets + sidebar/top-bar search), `frontend/src/App.tsx` (routes /wallet, /wallet/:address, /analytics/wallet/:address BEFORE :assetParam; shell-B-only).
**Why**: build order Part 2 — public tier-1 headline panels + connect-wallet heavy tabs for any HL wallet, resolution via userRole cached permanently (60-weight paid once per address ever).
**What**: tier-1 recurring cold cost 44 weight (≤65 contract), warm 0; verified live on HL rank-#12 wallet (weight_cost 104 incl. one-time userRole; 3 positions w/ liq/ROE/funding/dated ages; equity all 8 period keys incl. perpDay..); all 5 tier-2 paths exercised on real payloads; Playwright capture explorer_p2_whale.png, 0 console errors. Deployed (MM gate running:0, prev tree explorer_p2_20260907_185557, dist overlaid no rm -rf, migration v18 applied 20:56:40 UTC).

## 2026-09-07 — Part 3: Perpl live state on the explorer + two label fixes
**Files**: backend/app/routers/explorer.py (/api/explorer/perpl/{address}: account id persisted forever, positions w/ formula liq price + liq_source, orders w/ SL/TP kinds, leaderboard ranks + 30-min top-20 snapshot history, history_note pre-indexer; _chain_call semaphore + 429 retry-with-jitter; unresolved-role fallthrough fix), backend/app/services/chain_reader.py (get_liquidation_info view + liq_price_for formula helper), backend/app/main.py (migration v19 perpl_account_id), backend/app/services/equity.py (label), frontend: WalletExplorerB venue switch + PerplPanel, explorerApi Perpl types, useCopyV1 perplOrders fetch, TraderProfileModal label fix + TP/SL column, TraderListRow/Discover/DiscoverB snapshot-cadence labels.
**Why**: build order Part 3. getLiquidationInfo is a MARKET param view (ABI read, not guessed) — exposed as liq_params; per-position liq price = documented formula w/ live maintenance margin (verified BTC short 79132.9 -> 85105.5 @ mm=2500).
**What**: deployed twice (both MM-gated), migration v19 21:10:54 UTC; prod evidence wallet 0x898a (acct #2118, all_vol #3, 132 snapshot appearances) + modal label fix screenshot. Defect found+fixed: Perpl-first visits froze HL role at unresolved.

## 2026-09-08 — Part 4: Envio HyperSync indexer + Perpl history + cross-venue activity
**Files**: indexer/perpl_indexer.py + perpl-indexer.service + README.md (NEW top-level), backend/app/main.py (migration v20 + /health perpl_indexer lag), backend/app/config.py (ENVIO_API_TOKEN field — pydantic crash-loop guard), backend/app/routers/explorer.py (/perpl/{addr}/history + /activity/{addr}), frontend explorerApi + WalletExplorerB (PerplHistoryCard, Activity tab, via_tx_from flags).
**Why**: build order Part 4; owner approved HyperSync-direct Python worker over full HyperIndex (8GB box, MySQL-native sink, same Envio engine — bounty note intact).
**What**: 23 ABI-exact event types, from-genesis backfill + 20s head-follow, layered attribution (OrderRequest context -> tx_from-to-account; query-time via_tx_from flag suppresses other-account balances on operator-sent rows), CNS->USD, idempotent sink. Pipeline validated on 1,527 REAL decoded events (bounded public-RPC window). Deployed: service active, idling on note=awaiting ENVIO_API_TOKEN (PENDING owner, free); migration v20 23:04:33 UTC. Also PENDING: post-backfill reconciliation; ABI refresh for 4 unknown deployed-contract topics.

## 2026-09-08 — Part 5: public hardening + final verification
**Files**: backend/app/main.py (explorer 20/min/IP bucket in RateLimitMiddleware + cache headers), backend/app/routers/explorer.py (OG page endpoint /api/explorer/page/{addr} injecting per-wallet meta into the built SPA shell + shed logging), server nginx the legacy host (location ~ /wallet/0x… -> backend OG page; backup /root/nginx_terminal_backup_20260907_210932.conf).
**Why**: build order Part 5 — public endpoints must be throttled, cacheable, shed-first under load, and shareable.
**What**: prod-verified 429+Retry-After after 20/min; cache headers live; dev burst at forced ceiling 200 -> 6/8 shed with logs; OG card live w/ DB enrichment (rank #12); verification matrix cold-looked-up whale (44-weight recurring vs ~290 doc estimate) + HLP vault (role header) + Perpl-only admin (auto-switch) at 1440/390 both themes, 0 console errors. Agent-role walkthrough PENDING a known agent address.

## 2026-09-08 — Part 6: density upgrade to the layout reference + public tabs

**Files modified**
- `backend/app/routers/explorer.py` — removed `Depends(get_authenticated_user)`
  from 7 endpoints (fills, ledger, funding, orders, extras, perpl history,
  activity); internal ledger reuse no longer forwards a user; unused
  `Depends`/`User`/`get_authenticated_user` imports dropped; Tier-2 docstring
  now says PUBLIC and lists the protections that stayed.
- `backend/app/main.py` — explorer responses are uniformly
  `Cache-Control: public, max-age=120` (nothing varies by `Authorization` now).
- `frontend/src/design-b/screens/WalletExplorerB.tsx` — rewritten to the layout
  reference: address header w/ badges + Watch/Alerts/Copy, five PnL stat cards
  (48h computed from `week.pnlHistory`), full-width equity chart with window
  pills / PnL-Value / Perp-only / hover crosshair / `$0` gridline / last-value
  label, three donut cards, nine public tabs, positions table with every
  reference column + per-row cohort context + build-history chevron opening the
  existing drawer, Perpl rendered as a second section below HL instead of a
  venue toggle.
- `frontend/src/design-b/tokens.css` — `xp-` layout classes (stat grid 5→2,
  donuts 3→1, dense table, tab strip, venue header, foot), same tokens the
  reference declares; mobile guards for the 42-char address.
- `frontend/src/lib/explorerApi.ts` — comment: tabs are public now.
- `frontend/src/components/copy/TraderProfileModal.tsx` — `HistoryDrawer`
  exported (keyword only; no render change) so the explorer reuses it.
- `docs/design/wallet-explorer-layout.html` — the reference, placed in-repo.
- `WALLET_EXPLORER_REPORT.md` — Part 6 section with all measured numbers.

**Why**
All wallet data is public on both venues; the connect-wallet gate on the tabs
bought nothing and hid most of the page from anyone not signed in. The layout
reference (Coinglass density) is the visual contract for what the page should
show once unlocked.

**What changed, measured**
- 7 tab endpoints now return 200 with no `Authorization` header (verified live).
- Tier-1 cold weight unchanged at **44** (target held); first-ever address 104
  (`userRole` +60, once per address ever); first tab click 114 on a wallet with
  1,878 fills (base 20 + `ceil(1878/20)`).
- Burst test at forced ceiling 200: 8/8 explorer lookups shed, background
  untouched, 0 venue 429s; ceiling restored to 1000 and verified.
- 12 Playwright captures (3 wallets x 1440/390 x light/dark), 0 console errors.
- Both earlier label fixes verified intact against the CURRENT build.

**Defects fixed in-pass**
1. `.side` collided with the shell sidebar (`height:100vh`) → positions rows
   1000 px tall; renamed `.xp-side`.
2. Rank badge never rendered — ingest period key is `all`, not `all_time`.
3. `"balanced onUNI"` missing space.
4. Perpl venue header showed a bare `ok` for wallets with no indexed events.
5. 42-char address forced the page past a 390 px viewport.

**Known, not fixed (pre-existing)**
Shell B overflows horizontally on mobile: at 390 px viewport `scrollWidth` is
424 and `.content` is 424 on `/analytics`, `/strategies`, `/portfolio` and
`/wallet/…` alike. Shell-level, affects all six B screens, not authorised here.

**Deploy**
MM slots gated (`safeToRestart:true, running:0`) before each restart; backend
scp'd + `systemctl restart perpl-terminal`; frontend OVERLAY (never `rm -rf`).
Backend backup at `/root/predeploy_xp_density_20260908/`.
