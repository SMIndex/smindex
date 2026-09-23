# core module changelog (main.py / ws / hub / server ops)

## 2026-09-06 — API OOM kill loop: wallet_insights background refresh gated (D-79)
**Files**: `backend/app/services/wallet_insights.py` (`start()` returns when disabled), `backend/app/config.py` (`WALLET_INSIGHTS_ENABLED: bool = True`), prod `.env` (`WALLET_INSIGHTS_ENABLED=false`, backup `/root/env_backup_<ts>.env`).
**Why**: `perpl-terminal` was kernel-OOM-killed every ~18 min (16 restarts in 2 h, anon-rss ≈ 3.4 GB, 8 GB box with 4 GB swap in use): `_refresh_inner` does `fetchall()` on all 4,502,793 `leader_trade_events` rows (one wallet alone = 3,289,458) 120 s after every boot. Every background service (HL tracker = live liquidation feed, copy tracker, Telegram) died with it.
**What**: config gate only — the algorithm is untouched, the Insights page serves the 16,690 stored rows. Restarted after the MM slots gate (`safeToRestart: true, running: 0`); API up, RSS 212 MB at t+163 s, HL tracker ws connected (9 subscriptions). Pre-existing, unrelated: `_orderbook_refresh_loop` "dictionary changed size during iteration" at startup and httpx ReadTimeouts to Perpl context — not touched.

## 2026-09-03 — Disk cleanup + backup regimen (server housekeeping)
**Server**: <server>. **Why**: root fs at 95%/8G, wallet_insights failing errno-28 (disk full).
**Root cause**: MySQL binlogs at **49G** (`binlog_expire_logs_seconds=2592000` = 30d, no replication) — the real hog, far bigger than the ~11G of un-rotated per-deploy dumps.
**Deleted** (Perpl scope only — did NOT touch gamesol/ecom/monad/migration): 13 old `/root/backup_pre_*.sql` (~8.9G, kept `tier2A_20260831` as 2nd line); old prev-tree generations (kept newest each, ~90M); `backupV1_20260625`, `terminal_releases` (Aug-11 staged), `deploy_backups`; dropped scratch table `tmp_hf_dating_before` (content confirmed in the pre-design-b dump first).
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
