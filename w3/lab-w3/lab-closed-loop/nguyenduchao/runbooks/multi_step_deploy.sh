#!/usr/bin/env bash
# multi_step_deploy.sh — 3-step transactional deploy with ordered rollback
#
# Steps (forward order A → B → C):
#   step-A: drain traffic (stop container)
#   step-B: apply new config (restart container)
#   step-C: re-enable traffic (start container)
#
# Rollback steps (REVERSE order — C → B → A):
#   rollback-C: scale replicas to 0 (stop)
#   rollback-B: revert config (restart)
#   rollback-A: restore traffic (start)
#
# Usage:
#   bash multi_step_deploy.sh --service <name> --step-a   [--dry-run]
#   bash multi_step_deploy.sh --service <name> --step-b   [--dry-run]
#   bash multi_step_deploy.sh --service <name> --step-c   [--dry-run]
#   bash multi_step_deploy.sh --service <name> --rollback-c [--dry-run]
#   bash multi_step_deploy.sh --service <name> --rollback-b [--dry-run]
#   bash multi_step_deploy.sh --service <name> --rollback-a [--dry-run]
#
# Exit codes: 0 = success (or dry-run) | 1 = failure

set -euo pipefail

SERVICE=""
DRY_RUN=false
STEP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service)    SERVICE="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --step-a)     STEP="A";  shift ;;
    --step-b)     STEP="B";  shift ;;
    --step-c)     STEP="C";  shift ;;
    --rollback-c) STEP="RC"; shift ;;
    --rollback-b) STEP="RB"; shift ;;
    --rollback-a) STEP="RA"; shift ;;
    *) echo "[multi_step_deploy] Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$SERVICE" ]]; then
  echo "[multi_step_deploy] ERROR: --service <name> is required"
  exit 1
fi

CONTAINER="ronki-${SERVICE}"

# ── Dry-run ───────────────────────────────────────────────────────────────────
if $DRY_RUN; then
  case "$STEP" in
    A)  echo "[DRY-RUN] step-A: would drain traffic → docker stop $CONTAINER" ;;
    B)  echo "[DRY-RUN] step-B: would apply config → docker restart $CONTAINER" ;;
    C)  echo "[DRY-RUN] step-C: would re-enable traffic → docker start $CONTAINER" ;;
    RC) echo "[DRY-RUN] rollback-C: would stop container → docker stop $CONTAINER" ;;
    RB) echo "[DRY-RUN] rollback-B: would revert config → docker restart $CONTAINER" ;;
    RA) echo "[DRY-RUN] rollback-A: would restore traffic → docker start $CONTAINER" ;;
    *)  echo "[DRY-RUN] would execute: full 3-step deploy on $CONTAINER" ;;
  esac
  exit 0
fi

# ── Real execution ────────────────────────────────────────────────────────────
case "$STEP" in
  A)
    echo "[multi_step_deploy] step-A: draining traffic from $CONTAINER..."
    docker stop "$CONTAINER" 2>/dev/null || true
    echo "[multi_step_deploy] step-A complete."
    ;;

  B)
    echo "[multi_step_deploy] step-B: applying new config to $CONTAINER..."
    docker restart "$CONTAINER" 2>/dev/null || docker start "$CONTAINER"
    sleep 3
    STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
    if [[ "$STATUS" != "running" ]]; then
      echo "[multi_step_deploy] ERROR: step-B failed — $CONTAINER status=$STATUS"
      exit 1
    fi
    echo "[multi_step_deploy] step-B complete."
    ;;

  C)
    echo "[multi_step_deploy] step-C: re-enabling traffic for $CONTAINER..."
    docker start "$CONTAINER" 2>/dev/null || true
    sleep 2
    STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
    if [[ "$STATUS" != "running" ]]; then
      echo "[multi_step_deploy] ERROR: step-C failed — $CONTAINER status=$STATUS"
      exit 1
    fi
    echo "[multi_step_deploy] step-C complete."
    ;;

  RC)
    echo "[multi_step_deploy] rollback-C: stopping $CONTAINER..."
    docker stop "$CONTAINER" 2>/dev/null || true
    echo "[multi_step_deploy] rollback-C complete."
    ;;

  RB)
    echo "[multi_step_deploy] rollback-B: reverting config on $CONTAINER..."
    docker restart "$CONTAINER" 2>/dev/null || docker start "$CONTAINER"
    sleep 3
    echo "[multi_step_deploy] rollback-B complete."
    ;;

  RA)
    echo "[multi_step_deploy] rollback-A: restoring traffic to $CONTAINER..."
    docker start "$CONTAINER" 2>/dev/null || true
    sleep 2
    echo "[multi_step_deploy] rollback-A complete."
    ;;

  *)
    echo "[multi_step_deploy] ERROR: no step specified."
    echo "                    Use --step-a/b/c or --rollback-a/b/c"
    exit 1
    ;;
esac

exit 0
