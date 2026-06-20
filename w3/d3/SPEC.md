# AIOps Mini-Platform Spec — Nguyen Duc Hao

---

## 1. Platform Overview

This AIOps mini-platform provides automated anomaly detection, alert correlation, and root-cause analysis for a 10-service microservice stack deployed on Docker Compose (frontend, api-gateway, payment-svc, inventory-svc, notification-svc, checkout-svc, auth-svc, log-collector, dns-resolver, cache-svc). The platform ingests Prometheus time-series metrics, structured JSONL logs, and HTTP synthetic probe results to produce actionable incident signals with median detection latency under 40 seconds. The primary users are on-call SRE engineers who need a single `/rca` API endpoint to answer "what is broken and why" within 60 seconds of an alert firing, without manually correlating dozens of dashboards.

---

## 2. SLO Definition (from W3-D1)

Defined in `w3/d1/slo_spec.yaml`. Three services are instrumented with full SLO + burn-rate alerting:

### api — Availability SLO

| Field | Value |
|-------|-------|
| SLI name | `api_availability` |
| SLI kind | availability |
| SLI formula | `count(status NOT IN {5xx, 429} AND latency_ms < 500) / count(all_requests)` |
| SLO target | **99.9%** (3 nines) |
| Window | 30 days |
| Error budget | 20,738 failures/month (~23 minutes downtime equivalent) |
| Burn-rate alert Tier 1 | threshold 14.4 × (1h short + 5m long window) — page immediately |
| Burn-rate alert Tier 2 | threshold 6 × (6h short + 30m long window) — page if sustained |
| Burn-rate alert Tier 3 | threshold 1 × (3d short + 6h long window) — ticket only |

### db — Latency SLO

| Field | Value |
|-------|-------|
| SLI name | `db_query_success` |
| SLI kind | latency |
| SLI formula | `count(success = true AND duration_ms < 100) / count(all_queries)` |
| SLO target | **99.95%** (3.5 nines) |
| Window | 30 days |
| Error budget | 863 failed queries/month (~22 minutes downtime equivalent) |

### frontend — Composite Availability SLO

| Field | Value |
|-------|-------|
| SLI name | `frontend_page_load_ok` |
| SLI kind | availability |
| SLI formula | `count(dom_ready_ms < 3000 AND js_error = false AND network_error = false) / count(all_page_views)` |
| SLO target | **99.0%** (2 nines) |
| Window | 30 days |
| Error budget | 51,840 page-view failures/month (~432 minutes downtime equivalent) |

**Validation result** (`w3/d1/validation_report.json`): noise_reduction = 86.4%, MTTD delta = 60s, false negatives = 0, verdict = **PASS**.

---

## 3. Detection + Correlation + RCA Stack (from W1 + W2)

### Detection Layer

The anomaly detector combines three complementary algorithms in an ensemble, applied to Prometheus metric time series scraped every 10 seconds:

- **Isolation Forest** (scikit-learn): unsupervised outlier detection on 5-feature sliding windows (mean, std, min, max, trend slope). Low computational cost; effective for abrupt point anomalies (pod kill, instant CPU spike). False-positive rate ~5% on steady-state baselines.
- **3σ threshold detector**: rolling z-score with 5-minute window. Simple and interpretable; provides low-latency first-pass detection (MTTD p50 = 28s for hard failures). Tuned per-service from baseline data collected in W3-D2.
- **LSTM Autoencoder** (PyTorch): sequence-to-sequence reconstruction error detector trained on 24-hour rolling windows. Catches gradual degradation patterns (memory leak, slow disk fill) that point detectors miss. Higher latency (MTTD p50 = 47s for gradual faults) but essential for the §4 "capacity exhaustion" failure pattern.

Input sources: `http_requests_total`, `http_request_duration_seconds`, `container_cpu_usage_seconds_total`, `container_memory_usage_bytes`. Output: alert events with `{service, metric, anomaly_score, fire_ts}` emitted to the `/alerts` endpoint.

### Correlation Layer

The alert correlator groups related alerts into incident clusters using a graph-based community detection approach (W2-D1):

- **Time window**: alerts within ±120 seconds of each other are candidates for correlation
- **Service topology adjacency**: alerts on services connected in the call graph (topology.json) are preferentially grouped
- **Template similarity**: log templates (Drain parser, W1-D2) are matched with cosine similarity > 0.7 to detect same-root log anomalies across services
- Output: `cluster_summary.json` with `{cluster_id, services, alert_ids, start_ts, end_ts, likely_incident_type}`

### RCA Layer

Topology-aware RCA combining three signals (per ADR-007 in `w3/d1/DESIGN.md` and ADR-001 in `w1/day-3/ADR-001.md`):

1. **Topology distance from edge** (upstream-bias): services closer to the ingress (depth 0 = api-gateway, frontend) are preferred as root candidates when their alert count is non-trivial. Prevents "pick the loudest downstream" trap (Experiment 10 in W3-D2).
2. **First-drift time** (causal lag): the service whose metric first deviated from baseline is upstream-biased via Granger causality test (W2-D2). Tie-breaks topology depth ties.
3. **Alert volume** (tiebreaker only): used only when topology depth and first-drift time are equal.

Known limitation: infrastructure-tier services (dns-resolver, cache) must be classified with `tier: infra` in topology.json to receive synthetic depth 0. Without this classification, RCA may prefer the loudest app-tier service over an infrastructure dependency (Experiment 9 miss in W3-D2, Gap 2 in chaos_report.md).

---

## 4. Reliability Validation (from W3-D2)

Full chaos engineering report: `w3/d2/w3-d2-pack/chaos_report.md`.

### Scoreboard

| Metric | Value |
|--------|-------|
| Total experiments | 10 |
| Detected | 9/10 (90%) |
| RCA correct | 8/9 (89%) |
| False alarms in baseline | 0 |
| MTTD p50 | 36s |
| MTTD p95 | 57s |
| Precision | 1.00 |
| Recall | 0.90 |
| Verdict | **PASS ✓** |

Acceptance thresholds: detect ≥ 7/10 ✓ | RCA ≥ 70% ✓ | FA ≤ 1 ✓

### Top 3 Gaps

1. **Semantic fault blindness** (Experiment 6 — auth_svc_clock_skew): Clock skew does not affect HTTP performance metrics. Pipeline completely blind to JWT validation failures, cert expiry, and clock drift. Fix: add 4xx tracking + `node_timex_offset_seconds` scrape.

2. **Infrastructure service RCA misconfiguration** (Experiment 9 — dns_resolver_slow): DNS resolver modeled as app-tier child of api-gateway; RCA picks api-gateway (louder symptom) instead of dns-resolver (root). Fix: `"tier": "infra"` classification gives synthetic depth 0 to infrastructure services.

3. **Slow MTTD for gradual faults** (Experiments 5, 7, 9): Memory fill (47s), disk fill (58s), DNS slow (52s) have highest MTTDs because gradual resource exhaustion is slow to cross reactive thresholds. Fix: add `predict_linear()` forward-projection alerts for proactive detection.

---

## 5. Operational Pattern (from W3-D3)

### Reproduced Outage

**Outage:** Cloudflare WAF Catastrophic Backtracking (2019-07-02, 27 minutes, failure mode: `catastrophic_backtracking`)

**Key learning:** A single O(2^n) regex in a middleware that processes every HTTP request can saturate all available CPU within 4 seconds of a global deploy, causing complete service unavailability with no graceful degradation. The AIOps runtime pipeline detected the fault at MTTD = 4s (HighLatency + CPUSaturation alerts), but the first 4 seconds of CPU saturation at Cloudflare's scale represent ~28 million failed requests. Runtime detection is necessary but insufficient for this failure class.

**Postmortem:** `w3/d3/postmortem.md`

**ADR reference:** `w3/d3/ADR.md` (ADR-008: Pre-Deploy Regex Complexity Gate) — the primary architectural decision derived from this outage. Closes Gap 2 from `rca_observed.json` by shifting defense from reactive (MTTD = 4s) to preventive (deploy-time static analysis).

### On-Call Model

Tier-based on-call rotation:
- **P0/SEV1** (SLO budget burn > 14.4×, or full service unavailability): immediate page to primary on-call + incident commander. Target MTTD < 30s, MTTR < 30min.
- **P1/SEV2** (burn rate 6–14.4×, or single-service degradation): page to primary on-call. Target MTTD < 60s, MTTR < 2h.
- **P2/SEV3** (burn rate 1–6×, or non-user-facing degradation): ticket creation, no immediate page. Review at next business day.

### ADR Repository

All architectural decisions are tracked in ADR files:
- `w1/day-3/ADR-001.md` — Kafka as central transport (W1-D3)
- `w3/d3/ADR.md` — Pre-deploy regex complexity gate (W3-D3, this lab)

---

## 6. Cost Model (from W3-D3)

Implemented in `w3/d3/cost_model.py`. Output for the current stack (80 services, fintech context):

```
Scenario 3 — Payment gateway fintech (80 svcs, 4 inc/mo × 1.5h, $30k/h, $40k AIOps)
Monthly value generated :  $ 72,000.00
Monthly AIOps cost      :  $ 40,000.00
ROI ratio               :       1.80x
Payback period          :   0.56 months (~17 days)
Verdict                 :  WORTH_IT
```

**Break-even analysis:**
- Current cost: $40,000/month (0.5 FTE SRE $10.4k + DataDog APM $12k + compute/storage $5k + Kafka infra $5k + model inference $4k + monitoring $3k)
- Break-even threshold: AIOps is worth deploying when `incidents_per_month × avg_duration_h × downtime_$/h × 0.4 > $40,000`
- At $30k/hr downtime cost: break-even at `incidents × duration_h > 3.33` (e.g., 3 incidents × 1.11h, or 4 incidents × 0.83h)
- The current stack (4 incidents × 1.5h) comfortably exceeds break-even.

**When NOT to deploy AIOps** (per §8.5):
- Stack has < 30 services and < 3 incidents/month → hire a good SRE instead
- Observability stack not mature (no SLO, no centralized logging) → AIOps has no clean signal to work with
- Postmortem culture not established → AIOps surfaces signals but nobody acts on them

---

## 7. Open Risks

| Risk | Severity | Status | Mitigation Plan |
|------|----------|--------|-----------------|
| **Semantic fault blindness**: JWT validation failures, cert expiry, clock skew are invisible to HTTP performance metrics. Estimated 10% of real-world incidents fall in this class. | HIGH | Open | Add 4xx error rate tracking; integrate `node_timex_offset_seconds` from node-exporter; add cert expiry alert (`ssl_certificate_expiry_days < 14`). Target: W4-D1. |
| **Infrastructure service RCA misconfiguration**: dns-resolver, cache-svc, and future infra services (secret store, config service) need explicit `tier: infra` topology annotation. Without this, RCA picks the loudest app-tier service as root for all infra-layer faults (Experiment 9, W3-D2). | HIGH | Open | Add `"tier": "infra"` to dns-resolver, cache-svc in topology.json; modify RCA engine to assign synthetic depth=0 to infra-tier services. Target: W4-D1. |
| **Gradual fault detection latency**: Memory leak, disk fill, and gradual DNS degradation have MTTD p50 of 47–58s vs 22–35s for hard failures. The 30s MTTD target (§9.4) is missed for this class. | MEDIUM | Open | Add `predict_linear()` proactive alerting tier for memory, disk, and latency trends. Reduces MTTD for gradual faults to < 30s by detecting trajectory before threshold crossing. |
| **No pre-deploy validation gate**: The Cloudflare regex outage (reproduced in W3-D3) demonstrated that runtime detection cannot prevent the first wave of degradation from a deploy-time fault. Any rule, config, or artifact deploying into production can contain latent bugs that the pipeline only catches after damage begins. | MEDIUM | Planned | Implement ADR-008 (pre-deploy regex complexity gate, W3-D3/ADR.md) as first step. Generalize to a "deploy gate" framework for WAF rules, alert rules, and PromQL expressions. |
| **AIOps monitoring stack self-dependency**: If Prometheus or the pipeline itself fails, all detection capability is lost simultaneously (§4 monitoring-loop pattern, Roblox 2021). The pipeline currently has no health monitoring for the pipeline itself. | LOW | Open | Add an external synthetic probe that monitors `/alerts` and `/rca` endpoints from outside the stack; alert if the probe cannot reach the AIOps pipeline for > 60s. This creates an independent watchdog that does not share the failure domain of the monitored services. |
