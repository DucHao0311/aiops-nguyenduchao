# W3-D2 Chaos Engineering — Starter Pack

This is a **starter pack** (scripts + templates + runner skeleton). It does
NOT ship the 10-service docker stack referenced in §8.1 of the material —
that stack must be brought from your own work, your trainer, or a future
release of this pack.

## What's inside

```
README.md                         this file
experiments_template.yaml         10-entry YAML — fill 2-9 yourself
synthetic_probe.sh                external steady-state probe (§6.4)
pipeline/chaos_runner_skeleton.py runner with 2 TODO functions (§8.5)
scripts/
├── start_stack.sh                stub — wire to your docker-compose
├── capture_baseline.py           N-min Prometheus snapshot → baseline.json
├── query_pipeline.py             call /alerts + /correlate + /rca
└── score_run.py                  scoreboard from chaos_results.json
configs/
└── prometheus_targets.yml        example scrape targets — adapt to your stack
```

## What's NOT included (build yourself or ask trainer)

- `docker-compose.yml` with 10 services (frontend, api-gateway, payment-svc,
  inventory-svc, notification-svc, checkout-svc, auth-svc, log-collector,
  dns-resolver, cache-svc + Prometheus + Grafana + Alertmanager)
- AIOps pipeline FastAPI service exposing /alerts, /correlate, /rca
- Pumba + Toxiproxy binaries (install separately: see §4)

## Quick test (without stack)

```bash
chmod +x synthetic_probe.sh scripts/*.sh
bash synthetic_probe.sh http://example.org probe.log &
sleep 30 && kill %1
head probe.log     # should show "pass" lines
```

## How to integrate with your own stack

1. Edit `scripts/start_stack.sh` — replace placeholder with `docker compose up -d` of your stack
2. Edit `configs/prometheus_targets.yml` — point at your service ports
3. Edit `pipeline/chaos_runner_skeleton.py` — fill 2 TODO functions per §8.5
4. Fill experiments_template.yaml entries 2-9 per §8.4
5. Run: `bash scripts/start_stack.sh && python scripts/capture_baseline.py --duration 300 --out baseline.json`
