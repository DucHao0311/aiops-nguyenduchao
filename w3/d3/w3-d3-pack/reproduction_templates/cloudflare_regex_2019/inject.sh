#!/usr/bin/env bash
# Flip the middleware to active — every request now triggers catastrophic backtracking.
docker compose exec -e EVIL_REGEX_ACTIVE=1 api sh -c "pkill -USR1 -f uvicorn || true"
# Force restart so env var is re-read
EVIL_REGEX_ACTIVE=1 docker compose up -d --force-recreate api
echo "[$(date -u +%H:%M:%S)] WAF rule now active — try: curl --max-time 30 'http://localhost:8888/?q=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx='"
