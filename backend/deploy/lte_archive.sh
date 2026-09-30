#!/bin/bash
# Archive leader_trade_events rows that are about to pass the 30-day retention
# (pending item 4, 2026-09-28). One gzip per run covering the id range
# [last archived to_id + 1, first id newer than 30 days). Only after the gzip
# is verified is the range recorded in lte_archive_log; the API's retention
# step deletes nothing above MAX(lte_archive_log.to_id), so a failed archive
# stops deletion instead of losing rows.
# Installed at /root/backups/lte_archive.sh, cron 40 2 * * * (before the
# 03:30 nightly dump).
set -euo pipefail
DB=perpl_terminal
DIR=/root/backups/lte_archive
RETENTION_DAYS=30
mkdir -p "$DIR"

q() { mysql -N -B "$DB" -e "$1"; }

FROM=$(q "SELECT COALESCE(MAX(to_id), 0) + 1 FROM lte_archive_log")
CUT=$(q "SELECT id FROM leader_trade_events WHERE detected_at >= UTC_TIMESTAMP() - INTERVAL $RETENTION_DAYS DAY ORDER BY detected_at LIMIT 1")
if [ -z "$CUT" ]; then
  echo "$(date -u) lte_archive: no row newer than ${RETENTION_DAYS}d, nothing to bound — skipped"
  exit 0
fi
TO=$((CUT - 1))
if [ "$TO" -lt "$FROM" ]; then
  echo "$(date -u) lte_archive: nothing new to archive (from=$FROM cut=$CUT)"
  exit 0
fi

ROWS=$(q "SELECT COUNT(*) FROM leader_trade_events WHERE id BETWEEN $FROM AND $TO")
OUT="$DIR/leader_trade_events_${FROM}_${TO}_$(date -u +%Y%m%d_%H%M).sql.gz"
nice -n 10 mysqldump --single-transaction --no-create-info --skip-triggers \
  --where="id BETWEEN $FROM AND $TO" "$DB" leader_trade_events | gzip > "$OUT"

if ! zcat "$OUT" | tail -1 | grep -q "Dump completed"; then
  echo "$(date -u) lte_archive: DUMP INCOMPLETE $OUT — not recorded, retention will not delete" >&2
  exit 1
fi
q "INSERT INTO lte_archive_log (archive_file, from_id, to_id, row_count, created_at)
   VALUES ('$OUT', $FROM, $TO, $ROWS, UTC_TIMESTAMP())"
echo "$(date -u) lte_archive ok: ids $FROM..$TO rows=$ROWS size=$(du -h "$OUT" | cut -f1) $OUT"
