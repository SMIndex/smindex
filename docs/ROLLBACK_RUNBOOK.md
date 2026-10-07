# SMINDEX rollback and restore runbook (current as of 2026-10-07)

Server `<server>` (root, SSH key only: `ssh -i ~/.ssh/nadmail_server root@<server>`).
App tree `/var/www/terminal` (not a git repo). API unit `perpl-terminal` (127.0.0.1:8002).
Older snapshot-specific runbook: `RESTORE_RUNBOOK.md` (pre-Design-B tag, 2026-09-03).

## Rules that apply to every step
1. **MM gate before ANY API restart** (the box hosts live market-making bots):
   ```bash
   set -a; . <mm-service>/.env; set +a
   curl -s -H "authorization: Bearer $<mm-admin-token>" http://127.0.0.1:8090/mm/api/admin/slots
   ```
   Restart only on `safeToRestart: true` and `running: 0`. Otherwise stop and tell the owner who is running.
2. **Frontend: overlay, never delete.** Old hashed chunks must stay or open tabs 404.
3. Every deploy leaves a backup in `/root/predeploy_YYYYMMDD[_tag]/`:
   `backend_files.tgz` (changed backend files + `backend/.env`, mode 600) and `index.html*` (the frontend entry before the overlay).
4. After any rollback: run the checks in "Verify" and note what was rolled back in the module changelog.

## A. Frontend rollback (no restart)
The previous build's chunks are still in `dist/assets` (overlay deploys), so restoring the old `index.html` is enough.
```bash
ssh -i ~/.ssh/nadmail_server root@<server>
ls -la /root/predeploy_*/index.html*                      # pick the one from before the bad deploy
grep -o 'assets/index-[A-Za-z0-9_-]*\.js' /root/predeploy_20261007/index.html   # its entry chunk
ls /var/www/terminal/frontend/dist/assets/<that entry>     # must exist (it does with overlay deploys)
cp /var/www/terminal/frontend/dist/index.html /root/rollback_from_$(date -u +%Y%m%d_%H%M).html
cp /root/predeploy_20261007/index.html /var/www/terminal/frontend/dist/index.html
```
Users get it on their next navigation (the service worker fetches `index.html` network-first, no cache clear needed).
If the old entry chunk is missing, rebuild that commit locally (`git checkout <sha> -- frontend && cd frontend && npx vite build`) and overlay `dist/assets/.` then `index.html`.

## B. Backend rollback (restart, MM gate first)
```bash
ssh -i ~/.ssh/nadmail_server root@<server>
cd /var/www/terminal
tar tzf /root/predeploy_20261007/backend_files.tgz            # see what it contains
tar czf /root/rollback_from_$(date -u +%Y%m%d_%H%M).tgz $(tar tzf /root/predeploy_20261007/backend_files.tgz)   # keep the current version
tar xzf /root/predeploy_20261007/backend_files.tgz -C /var/www/terminal   # restores those files (incl. backend/.env)
# files ADDED by the bad deploy are not in the tarball: remove them only if they break the import
#   (e.g. backend/app/routers/beta.py for the 2026-10-07 deploy), then:
venv=/var/www/terminal/backend/venv/bin/python; (cd backend && $venv -c "import app.main")   # must import cleanly
# MM gate (rule 1), then:
systemctl restart perpl-terminal
```
New DB tables from a deploy (e.g. `user_risk_ack`, `beta_feedback`) are created with IF NOT EXISTS and are harmless to leave.

## C. Database restore
Nightly dumps: `/root/backups/nightly/daily/` (7 kept) and `/root/backups/nightly/weekly/` (Sunday copy, 4 kept); each ends with `-- Dump completed`.
Off-server copy: `bash scripts/pull_backup.sh` on the owner's PC -> `E:\smindex-backups` (newest weekly, verified).
Restore into a NEW database first, check, then switch:
```bash
F=/root/backups/nightly/daily/perpl_terminal_YYYYMMDD_0330.sql.gz
free -m        # swap under 85 % before starting (heavy job)
mysql -e "CREATE DATABASE perpl_restore"
zcat $F | nice -n 19 ionice -c3 mysql perpl_restore          # ~2-3 h on this box under load
# compare row counts of the big tables with prod / the dump, then:
# MM gate, stop the API, point DATABASE_URL at perpl_restore (or rename schemas), start the API
```
Tested 2026-10-07: see `audits/BETA_READINESS_REPORT.md` section 2.2.

## D. Environment (`backend/.env`) restore
Every predeploy tarball contains `backend/.env`. Restore just that file:
```bash
tar xzf /root/predeploy_20261007/backend_files.tgz -C /tmp backend/.env && install -m 600 /tmp/backend/.env /var/www/terminal/backend/.env
# MM gate, then: systemctl restart perpl-terminal
```
pydantic Settings crash-loops on UNKNOWN keys: never add a key that is not declared in `backend/app/config.py`.

## E. nginx config rollback
Backups: `/root/etc-backups/nginx-*.bak-YYYYMMDD*`. Restore, then `nginx -t && systemctl reload nginx` (no API restart).

## Verify (after any rollback or restore)
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://smindex.xyz/                 # 200
curl -s -o /dev/null -w "%{http_code}\n" https://smindex.xyz/api/markets      # 200
grep -E "Traceback|ERROR" /var/log/perpl-terminal.log | tail                 # nothing new
systemctl status smindex-monitor.timer                                       # active; it alerts the admin chat
```
