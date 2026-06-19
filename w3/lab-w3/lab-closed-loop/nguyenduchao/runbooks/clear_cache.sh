#!/usr/bin/env bash
# clear_cache.sh — flush in-memory cache of a service
#
# Implementation: send SIGHUP to the container process.
# Many servers reload config / flush caches on SIGHUP.
# For services exposing /admin/cache/clear, extend the HTTP call below.
#
# Usage:
#   bash clear_cache.sh --service <name> [--dry-run]
#
# Exit codes: 0 = success (or dry-run) | 1 = failure

set -euo pipefail

SERVICE=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)  SERVICE="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=true; shift ;;
    *) echo "[clear_cache] Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "[clear_cache] ERROR: --service <name> is required"
  exit 1
fi

CONTAINER="ronki-${SERVICE}"

if $DRY_RUN; then
  echo "[DRY-RUN] would execute: docker kill --signal=SIGHUP $CONTAINER"
  exit 0
fi

if ! docker inspect "$CONTAINER" > /dev/null 2>&1; then
  echo "[clear_cache] ERROR: container $CONTAINER not found."
  exit 1
fi

echo "[clear_cache] Sending SIGHUP to $CONTAINER to flush cache..."
docker kill --signal=SIGHUP "$CONTAINER"
echo "[clear_cache] SIGHUP sent to $CONTAINER — cache flush triggered."
exit 0
