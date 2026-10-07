# core module changelog (main.py / ws / hub / server ops)

## 2026-10-07 — Beta readiness pass (audits/BETA_READINESS_REPORT.md)
**Files**:
- `backend/app/main.py` (market-configs cache + stale-on-error; invite-code rate limit; strategies router behind `STRATEGIES_ENABLED`; beta router + migration v30)
- `backend/app/routers/{trades,terminal_stats,beta}.py`
- `backend/app/services/{chain_reader,ws_manager,perpl_client}.py`
- `backend/scripts/{authz_cross_user,beta_copy_switch_check,beta_auto_copy_switches,beta_telegram_check,smindex_monitor}.py`
- `backend/deploy/smindex-monitor.{service,timer}`, `scripts/pull_backup.sh`, `docs/ROLLBACK_RUNBOOK.md`
- server: nginx `snippets/smindex-security.conf`, `/mcp` 404 on both hosts, `CORS_ORIGINS`, logrotate stray file moved

**Why**: owner's beta GO/NO-GO pass.

**What**:
- **Security:** security headers + CSP; public MCP off; invite gate limited 10/10 min; trade-history validation (an `'OR 1=1--` row existed); terminal-stats admin-only; strategies API unmounted; CORS limited to our 3 hosts.
- **Reliability:** market-configs 30 s cache; log-noise downgrades (`AccountDoesNotExist` 0x03a0e277, Perpl REST timeouts); leaderboard parse off the event loop.
- **Monitoring:** every minute plus a daily summary.
- **Recovery:** off-server backup pull verified; rollback runbook tested.
- **Tests:** authz 68/68, copy gates, auto-copy, Telegram.
- **Deploy:** 2026-10-07 19:47:39 behind the MM gate.

## 2026-09-30 — Strategies removed, strat_ data truncated, <other-tenant> stopped, indexer off-peak window
**Files**: `backend/app/config.py` (`STRATEGIES_ENABLED=False`), `backend/app/services/hyperliquid/tracker.py` (strat_liquidations write gated), `backend/app/main.py` (strat_outbox loop gated), `indexer/perpl-indexer-{start,stop,guard}.{service,timer}`, `indexer/indexer_guard.sh`, `indexer/perpl-indexer.service` (comment); prod `.env` `STRATEGY_ENGINE_ENABLED=false`.
**Why**: owner Parts 2 and 3 (2026-09-30).
**What**:
- **Strategies data:** all 24 `strat_` tables dumped to `/root/backups/strat_tables_20260930.sql.gz` (23,159,905 B; dump counts = live counts), then truncated. 1,121,457 → 0 rows, 202 MB freed.
- **<other-tenant>:** the pm2 app was stopped, deleted from pm2 and pm2 saved; files kept. It had 0 live open positions; its last live trade was 09-29 13:52.
- **Throwaway artifacts:** `autocopy_*` DBs and the `acr` user dropped; `/tmp/acr*` removed.
- **Indexer:** runs 01:00–06:00 UTC via timers; the guard stops it if the rolling 15-min p95 exceeds 1.5 s.
- **Deploy:** restart 08:57:36 behind the MM gate.

## 2026-09-29 — Overnight pass: server memory, HIP-3 contexts, thin-book SMI, retention fix, Telegram alert settings
**Files**: `backend/app/services/analytics/position_sweep.py`, `backend/app/services/hyperliquid_client.py` (`get_dex_contexts`), `backend/app/config.py` (`SMI_THIN_BOOK_MIN_OI_USD`, `AUTO_COPY_ENABLED`), `backend/app/routers/analytics.py`, `backend/app/services/alerts/{prefs,gateway,account_watch,producers}.py`, `backend/app/services/telegram_queue.py` (`on_result`), `backend/app/services/telegram_bot.py`, `backend/app/routers/telegram.py`, `backend/app/main.py` (migrations v27 + v28), `indexer/perpl-indexer.service`, `scripts/publish_public_snapshot.py`; server: mysqld.cnf, `mysql.service.d/oom.conf`.
**Why**: overnight prompt, Parts A, B and E (report `audits/OVERNIGHT_REPORT.md`).
**What**:
- **Memory:** 14 <other-tenant>/<other-tenant> units stopped and disabled; MySQL `max_connections` 151→100 and tmp tables 16→8 MB (live + cnf); mysqld `oom_score_adj` −600. Swap went 81%→58%. The `<other-tenant>` leak was left for the owner.
- **Indexer:** resumed, then paused again at block 60,161,008 because API p95 reached 4.5 s.
- **LTE retention:** now acts on any cycle with undeleted archived ids (the once-a-day gate ran before the 02:40 archive).
- **HIP-3:** `metaAndAssetCtxs` per held builder dex (+80 weight/cycle); BRENTOIL now has a mark, funding and a real c5.
- **Thin-book SMI:** OI < $50K gets no pulse tile and a marker.
- **Telegram:** per-user alert prefs, gateway (toggle/dedupe/quiet hours/hourly limit + digest/delivery log), server-side TP/SL-fired, liquidation-warning and daily-summary producers. 12 real deliveries, message ids 30440–30451.
- **Tests and publisher:** 4 stale tests fixed to their current contracts; the publisher scrubs MM names and token name and forbids the admin wallet.
- Deployed 13:01 and 13:38 UTC, each behind the MM gate.

## 2026-09-06 — API OOM kill loop: wallet_insights background refresh gated (D-79)
**Files**: `backend/app/services/wallet_insights.py` (`start()` returns when disabled), `backend/app/config.py` (`WALLET_INSIGHTS_ENABLED: bool = True`), prod `.env` (`WALLET_INSIGHTS_ENABLED=false`, backup `/root/env_backup_<ts>.env`).
**Why**: `perpl-terminal` was kernel-OOM-killed every ~18 min (16 restarts in 2 h, anon-rss ≈ 3.4 GB, 8 GB box with 4 GB swap in use): `_refresh_inner` does `fetchall()` on all 4,502,793 `leader_trade_events` rows (one wallet alone = 3,289,458) 120 s after every boot. Every background service (HL tracker = live liquidation feed, copy tracker, Telegram) died with it.
**What**: config gate only — the algorithm is untouched, the Insights page serves the 16,690 stored rows. Restarted after the MM slots gate (`safeToRestart: true, running: 0`); API up, RSS 212 MB at t+163 s, HL tracker ws connected (9 subscriptions). Pre-existing, unrelated: `_orderbook_refresh_loop` "dictionary changed size during iteration" at startup and httpx ReadTimeouts to Perpl context — not touched.

## 2026-09-03 — Disk cleanup + backup regimen (server housekeeping)
**Server**: <server>. **Why**: root fs at 95%/8G, wallet_insights failing errno-28 (disk full).
**Root cause**: MySQL binlogs at **49G** (`binlog_expire_logs_seconds=2592000` = 30d, no replication) — the real hog, far bigger than the ~11G of un-rotated per-deploy dumps.
**Deleted** (Perpl scope only — did NOT touch <other-tenant>/ecom/monad/migration): 13 old `/root/backup_pre_*.sql` (~8.9G, kept `tier2A_20260831` as 2nd line); old prev-tree generations (kept newest each, ~90M); `backupV1_20260625`, `terminal_releases` (Aug-11 staged), `deploy_backups`; dropped scratch table `tmp_hf_dating_before` (content confirmed in the pre-design-b dump first).
**Binlogs**: `PURGE BINARY LOGS BEFORE 3d` (49G→6.1G), retention 30d→7d (persisted to mysqld.cnf). **Journal**: vacuum 3d (3.9G→524M).
**Result**: 95%→**58%** (62G free). MySQL + perpl-terminal healthy, API 200, /tmp writable (200MB test + forced on-disk temp-table query OK), no new errno-28.
**Prevent recurrence**: `/root/backups/nightly_dump.sh` (cron 03:30 UTC, 7 daily + 4 weekly, gzip, integrity-checked, 80% disk guard); `predeploy_dump.sh` (keeps last 2); `disk_health.sh` (80% alert, for day-2 greps).
**Flagged, NOT fixed (out of scope)**: wallet_insights has a SEPARATE bug — `Data too long for column 'symbol'` (1406), a market ticker exceeding column width, stale since 08-11; independent of disk. Off-server weekly copy proposed, not configured (needs a target/creds decision).

## 2026-09-22/23 — Full audit, BRENT page, publisher identity hardening

**Files modified/added**
- `audits/{CODE,CONCEPT,PERFORMANCE,SECURITY}_AUDIT.md`, `audits/AUDIT_SUMMARY.md` (new)
- `backend/scripts/brent_page.py` (new) — regenerates `dist/brent.json` on a 60s systemd timer
- `backend/scripts/fresh_shorts_scan.py` (new) — read-only large-fresh-short scanner
- `frontend/public/brent.html` (new) — polls `brent.json`, swaps fragments without reload
- `scripts/publish_public_snapshot.py` — pinned identity + pre-push identity gate
- `.claude/settings.json` (new, gitignored) — `includeCoAuthoredBy: false`
- Server: `/etc/systemd/system/smindex-brent.{service,timer}`

**Why**
Owner reported the app was slow and wanted it measured, and wanted the BRENT analysis as a page
on smindex.xyz rather than a Claude artifact. Separately, the public repo showed unwanted
contributors.

**What changed**
- Audit is read-only. One CRITICAL fix applied: `/var/www/terminal/backend/.env` was mode 644 on
  a host shared with 20 other services, exposing `JWT_SECRET` (HS256 — signing key is also the
  verification key). Set to 600; API verified HTTP 200 after. **Secret rotation outstanding.**
- BRENT page needed no API restart and no nginx change: the smindex vhost's
  `try_files $uri $uri/ /index.html` serves real files from `dist/` directly.
- Publisher: git *config* was being overridden by `GIT_AUTHOR_*`/`GIT_COMMITTER_*` env vars.
  Now strips them, pins `rustsol <dev@smindex.xyz>`, strips attribution trailers, and refuses to
  push unless the written commit object passes the identity gate.

**Measured (see audits/PERFORMANCE_AUDIT.md)**
- `innodb_buffer_pool_size` 128 MB vs 13.66 GB data; `mysqld` 896 MB in swap; 221 MB RAM free;
  load 12.56 on 4 cores; 20 non-SMINDEX services on the box.
- `/api/strategies`: 642 queries, 11,962 ms DB, 9.59 s wall (worst 58 s).
- `SELECT COUNT(*), MAX(ts) FROM <feed table>` examined 1,343,729,898 rows — `scheduler.py:68`,
  `routers/strategies.py:216`.
- `trader_profiles` refresh avg 245 s; `hl_fill_events` aggregates avg 33 s; `analytics_positions`
  DELETE avg 48 s; `DISTINCTROW symbol LIKE` avg 97 s — all missing one-line indexes.
- HTTP/2 compiled in but not enabled; nginx has never logged `$request_time`.
- Monad RPC p50 83 ms but 20% HTTP 429.

**Ordering traps recorded**
Move SIWE nonces off the process-local dict (`auth.py:89`) before adding uvicorn workers, or
~half of logins 401. Free memory before raising the buffer pool.

## 2026-09-23 — /opt log rotation + dead-run cleanup (prod, ops only)
- Deleted `<mm-service>/data/BTC/{book,trade}.zombie-20260812-090216.jsonl` (245 MB, not open by any process).
- Copy-truncate rotated (writers hold O_APPEND, no process touched): `<mm-service>/data/web.log` 482 MB → `web.log.20260923.gz` 21 MB; `<mm-service>/data/measure-mainnet.log` 285 MB → `.20260923.gz` 39 MB. No <other-tenant> log >100 MB.

## 2026-09-24 — nginx: request_time logging + HTTP/2 (audit P-14, P-13)
**Files modified:** prod `/etc/nginx/conf.d/smindex_timed_log.conf` (new), `/etc/nginx/sites-available/{smindex,the legacy host}`; repo copies `docs/smindex-nginx.conf`, `docs/smindex-nginx-timed-log.conf`. Backup `/root/nginx_backup_20260924/`.
**Why:** no latency data had ever been logged; HTTP/2 was compiled in but off.
**What changed:**
- `log_format smindex_timed` (adds host, proto, `rt=$request_time`, `urt=$upstream_response_time`); both vhosts write it to `/var/log/nginx/smindex_timed.log` AND keep the existing `access.log` (covered by the existing `/var/log/nginx/*.log` logrotate).
- `listen 443 ssl http2` on both vhosts. nginx 1.24 sets http2 per socket, so it is ON for every vhost on *:443 (owner approved). All 17 hostnames return the same status before/after; the 6 `000` are legacy domains not resolving here (pre-existing).

## 2026-09-24 — BRENT page: concurrent HL calls via shared client, 5-min cadence, "data as of" stamp
**Files modified:** `backend/scripts/brent_page.py`, `frontend/public/brent.html`, `backend/deploy/smindex-brent.{service,timer}` (new repo copies of the live units). Backup `/root/predeploy_20260924/`.
**Why:** owner decision — the insider hunt concluded (MMs, no insider), so 1-min cadence is not needed; a stalled run must be visible.
**What changed:**
- The 22 `clearinghouseState` + 1 `metaAndAssetCtxs` calls now go through `app.services.hyperliquid.client.post_info(priority=explorer)`, 4 concurrent (HL 429s at low concurrency, D-noted 2026-08-19), retry with 5/10/15 s backoff to outlast the explorer breaker. NOTE: the client's budget window is in-process, so this is Brent's OWN window, not the API's (owner chose this over moving the job into the API). Cost 64 weight/run.
- Measured: 12.2 s → 7.0–7.8 s per run (HL 7.2 s serial → <1 s; remaining ~5 s is DB). Output identical to the old job (14 short / 4 long / 22 wallets / 43 trips).
- Timer 60 s → 300 s; `TimeoutStartSec` 55 → 240 (still < cadence, no overlap).
- Page: badge now reads "data as of HH:MM:SS UTC · Ns ago"; stale after 420 s (one missed run), dead after 900 s, prefixed "STALE ·"; failed fetches keep the last stamp.

## 2026-09-24 — C-08 fix (both MARKETS iteration sites) + JWT_SECRET rotation (audit S-01)
**Files modified:** `backend/app/main.py` (orderbook refresh thread: `for mid in list(MARKETS)`), `backend/app/services/trader_tracker.py` (`_read_positions`: `list(MARKETS.items())`); prod `.env` JWT_SECRET. Backups `/root/predeploy_20260924/` (incl. `env.pre_jwt_rotation`, 600).
**Why:** 48 logged "dictionary changed size during iteration" 2026-07-20→09-23 (24 from trader_tracker.py:162, rest from the orderbook loop); .env was 644 on a 20-tenant box so JWT_SECRET must be treated as exposed.
**What changed / verified:**
- One API restart 11:40:18 UTC after MM gate `safeToRestart:true, running:0, sessions:0`; API healthy in 15 s, 0 tracebacks since.
- New JWT_SECRET = secrets.token_urlsafe(64), never printed. Token minted with the old secret: 200 before → 401 "Signature verification failed" after, both hosts.
- Fresh SIWE login (throwaway key, full nonce→sign→/connect) works on smindex.xyz and the legacy host; new token → /api/admin/me 200. Test created users row: 135	0xee40c92217775f00dd02136d89f4aed6bd884c50	2026-09-24 11:41:16.
- SIDE EFFECT: Fernet key for stored Perpl sessions is derived from JWT_SECRET (`auth_service.py:26`), so the 2 users with a stored Perpl session must reconnect Perpl once (no server-side process uses those sessions). MCP stage signatures in flight were invalidated (short-lived).

## 2026-09-24 — Housekeeping: delta_bot archive removed, journald cap verified, logrotate for Perpl app logs
- **delta_bot:** DB was already dropped and removed from `nightly_dump.sh` on 2026-09-23. Deleted the remaining final archive `/root/backups/delta_final_20260923/` — `delta_bot_20260923.sql.gz` 1.80 GB (68 tables incl. `encrypted_credentials`, `exchange_connections`, `momentum_*`, `fills`, `positions`; dump completed 2026-09-23 08:51) + `var_www_delta_20260923.tar.gz` 212 MB (code) + status files. No other `*delta_bot*` file >1 MB on the box. Nothing kept on the server (owner order: project abandoned).
- **journald:** `SystemMaxUse=500M` under `[Journal]`; journald restarted 2 s after the conf change (09-18 11:24); `journalctl --disk-usage` = 478.4M → enforced.
- **logrotate:** new `/etc/logrotate.d/perpl-apps` (repo `backend/deploy/logrotate-perpl-apps`): `/var/log/perpl-terminal.log`, `/var/log/perpl-recorder.log` (su root syslog), `<mm-service>/data/web.log`, `<mm-service>/data/measure-mainnet.log`; daily, maxsize 100M, 14 kept, compress+delaycompress, copytruncate (all writers O_APPEND). First run done: terminal 196 MB + recorder 242 MB rotated, API log writing again.
- NOT managed (other tenants): `<other-tenant>/<other-tenant>/farm/server_fleet_agents.log` 2.1 GB, `<other-tenant>/mint.log` 130 MB, `<other-tenant>/logs/engine-2026-08-26.log` 68 MB, <other-tenant> gtobench/keeper logs.

## 2026-09-24 — Optimization pass (full report: audits/OPTIMIZATION_REPORT.md)
- Median p95 over 60 endpoints 1,306 → 725 ms, p50 287 → 120 ms, non-200 54 → 11 (bench-param 422s only). Instrument: `backend/scripts/bench_endpoints.py` (real traffic too thin — 31 req/50 min).
- DB: buffer pool 128M → 1024M (cnf backup .bak_20260924; swap-out still present — owner call to drop to 896M); slow log 100 ms permanent; indexes ix_lte_exch_wallet_ts (P-03 245 s → 0.15 s), ix_lte_exchange (MAX(id) 13.2 → 0.05 s), ix_hlf_coin_ts (Brent), ix_hlf_ts, ix_afe_time_notional (P-06 12.7 → 1.5 s), strat_* x2; ix_ap_wallet_cycle ADDED THEN REVERTED (P-05 48 s → 303-310 s).
- API: HL budget rebalance (explorer shed 79 % → 0 %), queue-wait + cache-hit metrics on /health, event-loop watchdog (found fill_stats O(n²) on the loop — fixed), strategies set-based list, feed-health loose-index, movers 60 s cache, batched /api/analytics/context (wallet page 91 → 6 calls).
- Chain: Multicall3 + RPC fallback (rpc3/rpc1/ankr); detail 42.8 → 6.6 RPCs, 1,395 → 238 ms; 10,255 fail-overs/hour observed (primary throttles).
- nginx: upstream keepalive for /api (TIME_WAIT 20 → 2). Frontend: first-load JS 214 → 140 KB gz, SW cache-first for hashed assets (v11), venue-frame refresh for positions + copy dashboard.
- Staged on prod, activate on next restart: main.py (v24 list), rpc.py (fail-over log rate-limit). v21-v23 migrations always fail with NameError (pre-existing, not fixed).
- Correction: strat_liquidations still collects via the copy tracker (API process) — the 09-23 "feeds frozen" statement was wrong for that table.

## 2026-09-25 — Restart, v21-v23 migration fix, buffer pool 896M, test user removed
- `backend/app/main.py`: v21-v23 used a bare `engine` never defined in main.py (NameError on every start since written); now `app.db.database.engine`, shared with v24. Prod file matched staged 48afc0e before overwrite; backup /root/predeploy_20260925/main.py.
- API restart 20:28:25 UTC after MM gate (safeToRestart:true, running:0, sessions:0); up in 15 s, 0 tracebacks. Logs: v21 "mode columns already >= 16", v22 "spread present", v23 "ix_afe_time present"; v24 silent (all present, ix_ap_wallet_cycle NOT recreated). rpc.py per-minute failover summary active (0 per-call lines from the new process).
- `innodb_buffer_pool_size` 1024M -> 896M live (resize completed 20:30:46) and in mysqld.cnf (backup /root/predeploy_20260925/mysqld.cnf). Sample after: swap-out 0 KB/s, swap-in 8 KB/s.
- Deleted users row 135 (throwaway wallet 0xee40c922…, JWT test): no FK constraints reference users, 0 rows in all 11 user_id columns; row dumped to /root/predeploy_20260925/users_row135.sql first. users 30 -> 29.

## 2026-09-28 — Loop-stall fixes (smi_study + sweep's SMI compute) + P-05 downsample rewrite
**Files modified:** `backend/app/services/analytics/smi.py`, `smi_study.py`, `position_sweep.py`. Commits `16cefc2`, `227bac4`. Backups `/root/predeploy_20260928/`.
**Why:** audit open items — event-loop stalls during background cycles (watchdog stacks blamed `smi.compute_cycle` 2.6-3s every 20-min sweep and `smi_study` compute 1.3-9.7s bursts; 38 stalls / 118.5s over 3 days) and the P-05 downsample DELETE, which had degraded to 73-132s per run every 20 min, examining 1.77M rows via the non-sargable `DATE(p.cycle_ts)` join.
**What changed / verified (all measured on prod):**
- `smi.py`: 30d leverage median now computed in SQL (window functions) — returns ~180 rows instead of decoding 255,703 rollup rows on the event loop. Verified 178/178 assets exact-match vs the old Python values on the live cycle before deploy. compute_cycle 48.4s → 8.24s; 0 smi.py stalls since restart. Trade: the SQL query costs ~7.4s DB-side vs 2.3s for the raw fetch, but runs off the API thread with no locks.
- `smi_study.py`: `compute_outcomes` pairing, `compute_stats` per-asset bucket/Spearman, and `compute_flip_accuracy` pairing moved to `asyncio.to_thread` (fill_stats pattern). Post-boot run produced identical output (179 assets, 7,415 stat rows). Stalls in the run window: 46 (old run) → 1 (1.47s, the per-asset obs fetch decode — the known "result decode on loop" class, needs workers/S-03, out of scope).
- `position_sweep.py` P-05: downsample rewritten as one keyed range DELETE per UTC day (PK leads with cycle_ts; EXPLAIN range/ref both sides), commit per day. First run: 76 slices, 12.0s total, worst single statement 0.41s, Lock_time ≤0.005s, 2,631 rows deleted — vs 73-132s in one statement. `downsample_ms` added to the cycle's retention stats.
- One restart through the MM gate (safeToRestart:true, running:0, sessions:0); API healthy, 0 errors since startup. SMI rows continuous across the code change (BTC lev_median 25.4955 → 25.4973).
- OPEN (standing): step-6 order-status ws push end-to-end timing — measure on the owner's next real test order.

## 2026-09-28 — Authorization audit (pending item 1): 7 holes closed
**Files modified:** `backend/app/services/sl_tp_service.py`, `backend/app/routers/{copytrade,telegram,markets,insights}.py`, `backend/app/mcp/{auth,stdio,resources}.py`, `backend/app/mcp/tools/{account,leaders}.py`. Backup `/root/predeploy_20260928_authz/files.tgz`.
**Why:** item 1 of the pending list — prove the owner check on every user-state route; live cross-user test (two throwaway SIWE users, schema-only DB) found leaks.
**What changed:** SL/TP trigger event now owner-only (`send_to_user`, was broadcast to anonymous subscribers); leader follower list only to the leader or an admin (count stays public to logged-in users); MCP private reads require `read` scope (10 sites); MCP env-token fallback only in the stdio process; `markets/refresh` and `telegram/queue-stats` admin-only; `insights/refresh` requires login.
**Deploy:** one API restart 14:45:35 UTC after MM gate (safeToRestart:true, running:0, sessions:0); healthy in ~20s, 0 errors after startup; anonymous probes on prod now 422. Full evidence: `audits/PENDING_ITEMS_REPORT.md` §1.

## 2026-09-28 — SIWE nonces moved to the DB (audit S-03, pending item 2)
**Files modified:** `backend/app/routers/auth.py`, `backend/app/main.py` (migration v25 `siwe_nonces`).
**Why:** the in-memory nonce dict makes ~half of all logins 401 the moment uvicorn runs 2+ workers.
**What changed:** `/auth/payload` inserts the nonce (10-min TTL, prunes expired); `/auth/connect` claims it with one atomic UPDATE (used=0→1, unexpired) then deletes it. Verified cross-process on a throwaway schema: 20/20 logins, replay/forged/expired → 401, concurrent double-claim [True, False].
**Workers:** NOT added — prod swap 3.97/4 GB with 1 worker, 4 cores at load 10.4. Leader lock deferred until RAM exists for a 2nd worker. Evidence: `audits/PENDING_ITEMS_REPORT.md` §2.

## 2026-09-28 — Perpl indexer option A + backfill resumed (pending item 5)
**Files modified:** `indexer/perpl_indexer.py`, `indexer/perpl-indexer.service`. Backup `/root/predeploy_20260928_indexer/`.
**Why:** owner decision A (2026-09-23): order churn was 99.4% of rows; indexer sat enabled-but-dead since 2026-09-08.
**What changed:** OrderPlaced/Cancelled/Changed no longer queried; OrderRequest queried + decoded for same-tx fill attribution but never stored; `INDEXER_BACKFILL_SLEEP` pacing (unit Environment, never backend/.env); unit Nice=19 / IOSchedulingClass=idle / CPUWeight=20. 5,007 leftover OrderPlaced rows archived (`/root/backups/perpl_events_orderplaced_20260928.sql.gz`) then deleted. Started 15:35 UTC, resumed at block 58,483,643; 349 blocks/s, ~39 h to head. API probe (`/root/api_probe.log`) shows no degradation attributable to it.

## 2026-09-28 — Dependency audit + safe upgrades (pending item 7)
**Frontend:** vite 6.4.1→6.4.3, postcss 8.5.6→8.5.28 (+browserslist, picomatch, nanoid) — in-range, build-only; rebuild produced the identical entry hash (no redeploy). npm high 12→7; the 7 left are wallet/signing stack (wagmi/viem/walletconnect) + axios, listed not upgraded. package-lock.json is gitignored (reproducibility gap, flagged).
**Backend (prod venv):** anyio 4.13.0→4.14.2 (critical), python-jose 3.3.0→3.5.0 (critical), pyasn1 0.6.3→0.6.4, python-multipart 0.0.20→0.0.31, aiohttp 3.13.5→3.14.3. Listed not upgraded: cryptography, aiomysql, starlette (needs FastAPI), mcp (recommend dedicated upgrade), ecdsa (no fix). Restart 15:45:55 UTC after MM gate; pip check clean; 0 tracebacks. Rollback freeze `/root/predeploy_20260928_items234/pip_freeze_before_upgrade.txt`.
**Audits:** SMI hand-recompute BTC/ETH/GAS all match (rollups from raw positions + c1–c5); copy order #22 traced through the Aug-19 gate ladder, sizing and PnL reproduce exactly. Evidence: `audits/PENDING_ITEMS_REPORT.md` §5–7.

## 2026-09-28 — INCIDENT: prod mysqld OOM-killed 16:31 UTC (48 s), indexer paused
- Kernel OOM-killed mysqld (1.8 GB anon) at 16:31:02; systemd restart, serving at 16:31:50. Same OOM already happened at 11:04 UTC before any change of this pass reached prod — box overcommitted (swap 3.97/4 GB, load 10-19 on 4 cores; other tenants hold most swap).
- Response: perpl-indexer `disable --now` (added ~100 MB + write load; paused at block 59,665,072, 160,893 rows added), latency probe stopped. No bots running (MM slots running 0). API recovered: 0 errors in a live 60 s window, DB endpoints 200.
- Owner decision needed before resuming the indexer: memory relief (tenants / mysqld cap) or slower pacing.
