#!/usr/bin/env bash
# Simulates the original mistyped command — meant to scope to billing only,
# but without --workdir filter it nukes everything in the compose file.
echo "[$(date -u +%H:%M:%S)] running typo-prone destructive command..."
docker compose stop --timeout 1
echo "[$(date -u +%H:%M:%S)] 3 services down — index + placement unavailable means S3 metadata + object location both broken"
