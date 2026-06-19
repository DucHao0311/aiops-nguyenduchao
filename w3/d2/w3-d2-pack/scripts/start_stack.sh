#!/usr/bin/env bash
# start_stack.sh — start the W3-D2 10-service chaos lab stack
# Usage: bash scripts/start_stack.sh [--build]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACK_DIR="$(dirname "$SCRIPT_DIR")"

echo "==> W3-D2 Chaos Lab Stack Startup"
echo "    Pack directory: $PACK_DIR"

# Optional: rebuild images
BUILD_FLAG=""
if [[ "${1:-}" == "--build" ]]; then
  BUILD_FLAG="--build"
  echo "    Mode: --build (force rebuild)"
fi

cd "$PACK_DIR"

echo ""
echo "==> Starting docker compose stack..."
docker compose up -d $BUILD_FLAG

echo ""
echo "==> Waiting for services to become healthy..."
TIMEOUT=120
ELAPSED=0
INTERVAL=5

services=(
  "http://localhost:8080/health"   # frontend
  "http://localhost:8081/health"   # api-gateway
  "http://localhost:8082/health"   # payment-svc
  "http://localhost:8083/health"   # inventory-svc
  "http://localhost:8084/health"   # checkout-svc
  "http://localhost:8000/health"   # aiops-pipeline
)

all_ok=0
while [[ $ELAPSED -lt $TIMEOUT ]]; do
  all_ok=1
  for url in "${services[@]}"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$url" 2>/dev/null || echo "000")
    if [[ "$code" != "200" ]]; then
      all_ok=0
      break
    fi
  done
  if [[ $all_ok -eq 1 ]]; then
    break
  fi
  echo "    Waiting... ($ELAPSED/${TIMEOUT}s)"
  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))
done

if [[ $all_ok -eq 0 ]]; then
  echo ""
  echo "ERROR: Not all services healthy after ${TIMEOUT}s."
  echo "       Check: docker compose ps"
  echo "       Logs:  docker compose logs --tail=20"
  exit 1
fi

echo ""
echo "==> All services healthy!"
echo ""
echo "    Service URLs:"
echo "      frontend:       http://localhost:8080"
echo "      api-gateway:    http://localhost:8081"
echo "      payment-svc:    http://localhost:8082"
echo "      inventory-svc:  http://localhost:8083"
echo "      checkout-svc:   http://localhost:8084"
echo "      aiops-pipeline: http://localhost:8000"
echo "      prometheus:     http://localhost:9090"
echo "      grafana:        http://localhost:3000  (admin/admin)"
echo "      alertmanager:   http://localhost:9093"
echo "      toxiproxy API:  http://localhost:8474"
echo ""
echo "==> Next steps:"
echo "    1. python scripts/capture_baseline.py --duration 300 --out baseline.json"
echo "    2. nohup bash synthetic_probe.sh http://localhost:8080/health probe.log &"
echo "    3. python pipeline/chaos_runner.py"
