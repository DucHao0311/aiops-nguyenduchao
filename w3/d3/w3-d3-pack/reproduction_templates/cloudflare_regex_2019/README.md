# Cloudflare WAF Regex (2019-07-02) — Catastrophic Backtracking Reproduction

**Original:** https://blog.cloudflare.com/details-of-the-cloudflare-outage-on-july-2-2019

A new WAF rule containing a regex with nested quantifiers caused catastrophic
backtracking. Every request triggered it; CPU pegged on edge nodes globally
within seconds. The deploy was global → no canary buffer.

## Failure mode
- **Class:** catastrophic backtracking on a hot path
- **Pattern:** global deploy with no canary

## Setup
```bash
docker compose up -d        # FastAPI service on :8888 with the evil middleware
curl http://localhost:8888/healthz   # responds < 50ms while regex is OFF
```

## Inject
```bash
bash inject.sh              # flips middleware to "active" — every request now pinned to CPU
time curl --max-time 30 "http://localhost:8888/?q=$(printf '%.0sxxxxx' {1..30})="
```

Expected: request takes 5-15 seconds to respond (or times out). Other requests
queue behind it. Service appears alive (no crash) but unresponsive.

## What to observe in your AIOps pipeline
- Does the detector catch p99 latency explosion?
- Does CPU saturation become the signal (or does pipeline only watch error rate)?
- Anti-pattern test: would your SLI count these requests as failures? (4xx/5xx vs. slow 200)
