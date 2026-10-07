#!/bin/bash
# Off-server copy of the SMINDEX database (beta readiness 2.3).
# Pulls the newest WEEKLY dump (Sunday copy of the nightly dump) from the server
# to this PC, verifies it (same byte size as on the server + gzip integrity +
# the mysqldump completion marker) and keeps the newest 4 locally.
#
# Run from Git Bash on the owner's PC (uses the existing SSH key):
#   bash scripts/pull_backup.sh                 # newest weekly dump
#   bash scripts/pull_backup.sh daily           # newest nightly dump instead
# Schedule weekly (Windows Task Scheduler, Mondays 09:00):
#   schtasks /Create /SC WEEKLY /D MON /ST 09:00 /TN "SMINDEX backup pull" ^
#     /TR "\"C:\Program Files\Git\bin\bash.exe\" -lc 'bash /e/wamp64/www/perpl/scripts/pull_backup.sh >> /e/smindex-backups/pull.log 2>&1'"
set -euo pipefail
KEY="${SMINDEX_KEY:-$HOME/.ssh/nadmail_server}"
HOST="root@<server>"
KIND="${1:-weekly}"
DEST="${SMINDEX_BACKUP_DIR:-/e/smindex-backups}"
KEEP=4
mkdir -p "$DEST"

REMOTE=$(ssh -i "$KEY" -o BatchMode=yes "$HOST" "ls -1t /root/backups/nightly/$KIND/perpl_terminal_*.sql.gz 2>/dev/null | head -1")
[ -n "$REMOTE" ] || { echo "$(date -u +%FT%TZ) no $KIND dump found on the server"; exit 1; }
NAME=$(basename "$REMOTE")
RSIZE=$(ssh -i "$KEY" -o BatchMode=yes "$HOST" "stat -c %s '$REMOTE'")

if [ -f "$DEST/$NAME" ] && [ "$(stat -c %s "$DEST/$NAME")" = "$RSIZE" ]; then
  echo "$(date -u +%FT%TZ) already have $NAME ($RSIZE bytes)"
else
  scp -i "$KEY" -o BatchMode=yes -q "$HOST:$REMOTE" "$DEST/$NAME.part"
  LSIZE=$(stat -c %s "$DEST/$NAME.part")
  [ "$LSIZE" = "$RSIZE" ] || { echo "$(date -u +%FT%TZ) SIZE MISMATCH $LSIZE vs $RSIZE"; exit 1; }
  gzip -t "$DEST/$NAME.part" || { echo "$(date -u +%FT%TZ) GZIP CHECK FAILED"; exit 1; }
  zcat "$DEST/$NAME.part" | tail -1 | grep -q "Dump completed" || { echo "$(date -u +%FT%TZ) NO COMPLETION MARKER"; exit 1; }
  mv "$DEST/$NAME.part" "$DEST/$NAME"
  echo "$(date -u +%FT%TZ) pulled $NAME ($RSIZE bytes) -> $DEST, verified (size, gzip, completion marker)"
fi
# keep the newest $KEEP local copies of this kind's dumps
ls -1t "$DEST"/perpl_terminal_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f -- "$old"; echo "removed old copy $(basename "$old")"; done
