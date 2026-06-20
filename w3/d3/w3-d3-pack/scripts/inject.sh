#!/usr/bin/env bash
# inject.sh — trigger the failure mode for the chosen outage.
set -e
OUTAGE="${1:?usage: bash inject.sh <outage_dir>}"
DIR="reproduction_templates/$OUTAGE"
SCRIPT="$DIR/inject.sh"
if [[ ! -f "$SCRIPT" ]]; then
  echo "ERROR: $OUTAGE has no inject.sh (design-only?)" >&2
  exit 1
fi
cd "$DIR"
bash inject.sh
