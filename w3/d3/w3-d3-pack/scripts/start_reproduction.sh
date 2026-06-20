#!/usr/bin/env bash
# start_reproduction.sh — bring up the chosen outage reproduction stack.
set -e
OUTAGE="${1:?usage: bash start_reproduction.sh <outage_dir>}"
DIR="reproduction_templates/$OUTAGE"
if [[ ! -d "$DIR" ]]; then
  echo "ERROR: no such reproduction dir: $DIR" >&2
  exit 1
fi
cd "$DIR"
if [[ ! -f docker-compose.yml ]]; then
  echo "NOTE: $OUTAGE is design-only — see $DIR/README.md, write SPEC instead." >&2
  exit 2
fi
docker compose up -d
echo "[$(date -u +%H:%M:%S)] $OUTAGE stack up — see docker compose ps"
