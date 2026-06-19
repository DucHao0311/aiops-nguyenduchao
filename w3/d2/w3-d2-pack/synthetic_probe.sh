#!/usr/bin/env bash
# synthetic_probe.sh — log pass/fail every 5s, use as steady-state signal (§6.4)
ENDPOINT="${1:-http://localhost:8080/checkout/health}"
LOG="${2:-probe.log}"
INTERVAL="${3:-5}"
THRESHOLD_MS="${4:-500}"
echo "# probe started at $(date -u +%s) endpoint=$ENDPOINT" >> "$LOG"
while true; do
  ts=$(date -u +%s)
  start=$(date +%s%N)
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$ENDPOINT" 2>/dev/null || echo "000")
  end=$(date +%s%N)
  latency_ms=$(( (end - start) / 1000000 ))
  if [[ "$code" == "200" && "$latency_ms" -lt "$THRESHOLD_MS" ]]; then
    echo "$ts pass $latency_ms" >> "$LOG"
  else
    echo "$ts fail $code $latency_ms" >> "$LOG"
  fi
  sleep "$INTERVAL"
done
