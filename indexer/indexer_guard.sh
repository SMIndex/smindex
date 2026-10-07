#!/bin/bash
# Latency guard for the off-peak indexer window. Every 5 min: 20 real API requests
# (4 endpoints x 5). p95 is taken over the last 3 runs (60 samples, 15 min) so one
# event-loop stall burst doesn't end the night; above LIMIT_MS the indexer is
# stopped until the next 01:00 UTC window.
LIMIT_MS=${LIMIT_MS:-1500}
LOG=/var/log/perpl-indexer-guard.log
STATE=/run/perpl-indexer-guard.samples
if [ "$(systemctl is-active perpl-indexer)" != "active" ]; then
  rm -f "$STATE"
  exit 0
fi
for ep in /api/markets /api/analytics/pulse /api/analytics/movers /api/traders; do
  for i in 1 2 3 4 5; do
    curl -s -m 30 -o /dev/null -w "%{time_total}\n" -H "Host: smindex.xyz" "http://127.0.0.1:8002$ep" >> "$STATE"
  done
done
tail -n 60 "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
read -r n p50 p95 max < <(sort -n "$STATE" | awk '{a[NR]=$1} END {i=int(NR*.95); if(i<1)i=1; printf "%d %.0f %.0f %.0f\n", NR, a[int(NR*.5)]*1000, a[i]*1000, a[NR]*1000}')
line="$(date -u '+%F %T') window_n=$n p50=${p50}ms p95=${p95}ms max=${max}ms load=$(cut -d' ' -f1 /proc/loadavg) swap=$(free | awk '/Swap/{printf "%d%%", $3*100/$2}')"
if [ "$n" -ge 40 ] && [ "$p95" -gt "$LIMIT_MS" ]; then
  systemctl stop perpl-indexer
  rm -f "$STATE"
  echo "$line -> STOPPED indexer (p95 > ${LIMIT_MS}ms), resumes at the next 01:00 UTC window" >> "$LOG"
else
  echo "$line ok" >> "$LOG"
fi
