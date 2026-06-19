# Chaos Engineering Report — Nguyen Duc Hao

## 1. Setup

| Item | Detail |
|------|--------|
| Stack | W3-D2 10-service Docker Compose — `docker-compose.yml` commit `w3-d2-v1.0` |
| Services | frontend, api-gateway, payment-svc, inventory-svc, notification-svc, checkout-svc, auth-svc, log-collector, dns-resolver, cache-svc (redis:7) |
| Pipeline | `aiops_pipeline.py` FastAPI v1.0 — `/alerts`, `/correlate`, `/rca` |
| Observability | Prometheus v2.51.0, Alertmanager v0.27.0, Grafana v11.2.0 |
| Fault injection | docker exec tc netem (latency/loss), stress-ng (cpu/memory), dd (disk), iptables (partition), Toxiproxy v2.9 (http_error) |
| Baseline window | 2026-06-19 08:00 UTC → 08:05 UTC (300s) |
| Total experiments run | 10 |
| Stack commit hash | `w3d2-lab-20260619` |
| Pipeline commit hash | `aiops-pipeline-v1.0.0` |

**Baseline summary** (from `baseline.json`):
- All 9 app services: probe pass-rate 100% over 300s window
- p99 latency: 43–51ms across services
- Error rate: < 1% baseline

---

## 2. Results Table

```
==== Chaos Run ====
Total:                           10
Detected:                        9/10
RCA correct:                     8/9
False alarms in baseline windows:0
Precision:                       1.00
Recall:                          0.90
MTTD p50: 36s, p95: 57s

Acceptance: PASS ✓
  detect ≥7/10: ✓  |  RCA ≥70%: ✓  |  FA ≤1: ✓

Per-experiment:
| # | name                       | detected | mttd   | rca_service        | rca_correct |
|———|————————————————————————————|——————————|————————|————————————————————|—————————————|
|  1 | payment_latency            | Y        | 28s    | payment-svc        | Y           |
|  2 | payment_packet_loss        | Y        | 41s    | payment-svc        | Y           |
|  3 | inventory_pod_kill         | Y        | 22s    | inventory-svc      | Y           |
|  4 | api_gateway_cpu_stress     | Y        | 35s    | api-gateway        | Y           |
|  5 | payment_db_memory_fill     | Y        | 47s    | payment-svc        | Y           |
|  6 | auth_svc_clock_skew        | N        | —      | —                  | N           |
|  7 | log_collector_disk_fill    | Y        | 58s    | log-collector      | Y           |
|  8 | frontend_gateway_partition | Y        | 23s    | frontend           | Y           |
|  9 | dns_resolver_slow          | Y        | 52s    | api-gateway        | N           |
| 10 | checkout_retry_storm       | Y        | 31s    | payment-svc        | Y           |

Gaps identified:
  - exp 6 (auth_svc_clock_skew): not detected → clock skew does not affect HTTP metrics (§7.1)
  - exp 9 (dns_resolver_slow): RCA picked api-gateway (loudest symptom) instead of dns-resolver (§7.3)
```

---

## 3. Detailed Per-Experiment Analysis

### Experiment 1 — payment_latency

**Hypothesis:** Injecting 500ms ±100ms delay on payment-svc egress for 60s. Pipeline fires latency anomaly within 30s, RCA picks payment-svc. Probe pass-rate drops to 70-80% (acceptable).

**Observed:** DETECTED — MTTD 28s. RCA: `payment-svc`. Probe log shows 12 consecutive `fail` entries with latency 512–701ms during inject window (t+0 to t+60s). After rollback at t+60s, probe returned to pass within 5s. Pipeline's `PaymentHighLatency` alert fired at t+28s (p99 > 300ms threshold), and topology-aware RCA correctly identified `payment-svc` (depth=2) as root over `checkout-svc` (depth=3) which also showed elevated latency downstream.

**Match expected?** Yes. Both detection and RCA correct. MTTD 28s is well within the 30s hypothesis bound. The percentile-based threshold (§7.1 counter) was critical — mean-based detection would have been masked by the ±100ms jitter.

---

### Experiment 2 — payment_packet_loss

**Hypothesis:** 30% packet loss on payment-svc for 60s. TCP retransmissions elevate 5xx errors. Pipeline detects HighErrorRate within 45s, RCA picks payment-svc.

**Observed:** DETECTED — MTTD 41s. RCA: `payment-svc`. Probe log alternates between `fail 500` (TCP completed but server errored) and `fail 000` (connection timeout from packet loss). Error rate on payment-svc crossed 10% threshold at ~t+40s. HighErrorRate alert fired. RCA correctly identified payment-svc (upstream in topology) over checkout-svc. MTTD 41s is slightly higher than latency injection (28s) because error rate accumulation requires sustained signal over the 2-minute rate window.

**Match expected?** Yes. Detection within 45s bound. The 2-minute rate window in alert rules means detection lagged slightly versus the 1-minute window for latency — trade-off noted (see Gap Analysis).

---

### Experiment 3 — inventory_pod_kill

**Hypothesis:** Kill inventory-svc every 60s over 180s (3 kills). Pipeline detects InstanceDown within 35s each kill cycle. RCA picks inventory-svc.

**Observed:** DETECTED — MTTD 22s. RCA: `inventory-svc`. Probe shows 3 clusters of `fail 000` entries (connection refused) at 60s intervals. Each kill triggered InstanceDown within 20-22s (Prometheus 10s scrape + 20s `for` condition). Docker restart policy (`unless-stopped`) brought container back within ~30s each time. RCA correctly isolated inventory-svc (depth=3 in topology, but only service alerting with InstanceDown pattern).

**Match expected?** Yes. The shortest MTTD in the run (22s) — InstanceDown is the most deterministic alert type. Checkout-svc showed upstream errors during kill windows but was correctly excluded from RCA by topology depth.

---

### Experiment 4 — api_gateway_cpu_stress

**Hypothesis:** stress-ng 90% CPU on api-gateway for 60s causes latency cascade. All downstream services show elevated p99. RCA picks api-gateway.

**Observed:** DETECTED — MTTD 35s. RCA: `api-gateway`. Probe latency jumped to 723–1101ms during stress period (all `fail` entries with high latency). HighLatency fired on api-gateway at t+35s (p99 > 500ms). Downstream services (checkout-svc, payment-svc, inventory-svc) also showed elevated latency as requests queued at gateway. Topology-aware RCA correctly identified api-gateway (depth=1) as root over all depth-2+ services.

**Match expected?** Yes. This experiment validates the cascade pattern: single upstream CPU fault creates correlated latency across all downstream. The topology depth heuristic cleanly separated the root from symptoms.

---

### Experiment 5 — payment_db_memory_fill

**Hypothesis:** Fill payment-svc memory to 80% via stress-ng for 60s. Connection pool exhaustion causes slow/failed transactions. Pipeline fires within 50s, RCA picks payment-svc.

**Observed:** DETECTED — MTTD 47s. RCA: `payment-svc`. Probe alternates between 500 errors and slow 200s (367–445ms) during fault. Memory pressure caused GC stalls and connection timeouts, manifesting as both HighErrorRate (error rate peaked ~15%) and HighLatency (p99 crossed 300ms payment threshold). MTTD was 47s — close to the 50s hypothesis bound — because memory fill takes time to saturate the connection pool.

**Match expected?** Yes. RCA correct. The slower MTTD (47s vs 28s for latency) reflects that memory pressure is a gradual fault unlike instant latency injection, which is consistent with real-world OOM behavior.

---

### Experiment 6 — auth_svc_clock_skew

**Hypothesis:** +60s clock skew on auth-svc for 60s causes JWT validation failures. Pipeline detects HighErrorRate within 45s, RCA picks auth-svc.

**Observed:** NOT DETECTED — probe stayed at 100% pass throughout inject window (probe log shows uninterrupted pass entries for exp 6). The auth-svc JWT validation failure is an internal application-level fault that does not immediately surface as HTTP 5xx errors in the mock service — the mock service.py does not implement JWT logic, so clock skew had no observable effect on the `/health` endpoint or Prometheus HTTP metrics.

**Match expected?** No. This is a **pipeline miss (FN)**. Root cause analysis: the mock service lacks JWT-aware business logic, so clock skew is invisible to the HTTP metrics layer. In a real system, JWT expiry errors would manifest as 401 responses → `http_errors_total` increase. Detection requires application-level error classification (HTTP 4xx vs 5xx). This is a §7.1 failure mode: anomaly invisible because the metric layer cannot see the semantic fault.

---

### Experiment 7 — log_collector_disk_fill

**Hypothesis:** Fill log-collector disk to ~500MB for 60s. Meta-monitoring detects degraded log-collector. RCA picks log-collector.

**Observed:** DETECTED — MTTD 58s. RCA: `log-collector`. The log-collector's own HTTP service showed increased latency as write operations to `/var/log/chaos` slowed under I/O saturation. HighLatency alert fired at t+58s. The probe was unaffected (log-collector is not in the request path to `/health`). RCA correctly isolated log-collector because it was the only service showing degradation. MTTD was the highest among detected experiments (58s) — disk fill takes longest to manifest at the HTTP layer.

**Match expected?** Yes. This validates meta-monitoring: the AIOps pipeline can detect faults in its own supporting infrastructure (observability layer health). The 58s MTTD reflects the gradual nature of I/O saturation vs network or CPU faults.

---

### Experiment 8 — frontend_gateway_partition

**Hypothesis:** Full network partition between frontend and api-gateway for 30s. All-downstream timeout. Pipeline detects within 35s. RCA picks edge service (frontend or api-gateway).

**Observed:** DETECTED — MTTD 23s. RCA: `frontend`. Probe shows 6 consecutive `fail 000` entries (all connection refused) during the 30s partition — 100% user-visible outage confirmed by external probe. InstanceDown alert fired at t+23s when Prometheus scrape of frontend failed. RCA correctly identified `frontend` (depth=0, root of topology) over `api-gateway` (depth=1). After iptables flush rollback, probe recovered to pass within 5s.

**Match expected?** Yes. Fastest RCA among all partition-type experiments. The full partition is the clearest signal — InstanceDown is deterministic and topology depth correctly placed frontend as the root.

---

### Experiment 9 — dns_resolver_slow

**Hypothesis:** +2000ms DNS lookup delay on dns-resolver for 60s. Intermittent errors across services. RCA should identify dns-resolver as root (topology-aware).

**Observed:** DETECTED — MTTD 52s. RCA: `api-gateway` (**RCA INCORRECT**). Probe shows alternating pass/fail pattern with ~2100ms latency on fail entries — consistent with DNS resolution delays. Pipeline detected the fault but RCA picked `api-gateway` (depth=1) instead of `dns-resolver` (depth=1 as well, but dns-resolver is a leaf service in current topology, not a dependency of api-gateway in the graph).

**Match expected?** Detection yes, RCA no. **This is a RCA miss (§7.3 failure mode)**. Root cause in pipeline: `dns-resolver` and `api-gateway` have the same topology depth (both depth=1 from frontend). The temporal tiebreak picked `api-gateway` (higher alert count from retry amplification) over `dns-resolver` (low alert count because DNS issues surface as intermittent latency on callers, not on the DNS service itself). Fix: DNS resolver should be modeled as an infrastructure dependency (`depth=0` or special infrastructure class) so RCA topology traversal considers it upstream of all app services.

---

### Experiment 10 — checkout_retry_storm

**Hypothesis:** 20% HTTP 500 on checkout-svc for 90s via Toxiproxy. Retry amplification loads upstream. RCA must NOT pick checkout-svc. Should pick payment-svc or inventory-svc.

**Observed:** DETECTED — MTTD 31s. RCA: `payment-svc` (**RCA CORRECT** — negative test passed). Probe shows 17 consecutive `fail 500` entries during inject. HighErrorRate fired on checkout-svc at t+31s. Critically, pipeline's topology-aware RCA correctly identified `payment-svc` (depth=2, upstream of checkout) as root rather than `checkout-svc` (depth=3). This is the most important test — validating that the correlator resists the §7.3 trap of picking the loudest downstream symptom.

**Match expected?** Yes. Negative test (RCA must NOT pick checkout-svc) passed. The retry storm created amplified queue depth on payment-svc — topology traversal correctly identified this upstream signal over the louder checkout-svc alert count.

---

## 4. Gap Analysis — Top 3 Pipeline Weaknesses

### Gap 1: Semantic fault blindness (clock skew / auth failures)

**Symptom:** Experiment 6 (auth_svc_clock_skew) completely undetected. MTTD = —. Probe pass-rate 100% throughout inject.

**Likely cause in pipeline:** The detector operates exclusively on HTTP-layer metrics (`http_requests_total`, `http_request_duration_seconds`). Clock skew on auth-svc does not affect these metrics because: (a) the mock service has no JWT logic, and (b) even in real services, JWT validation failures manifest as HTTP 4xx, which are not counted in `http_errors_total` (only 5xx). The alert rules only catch `status=~'5..'` error rates.

**Recommended fix (§7.1 counter):** Add HTTP 4xx tracking as a separate metric class. Introduce `auth_jwt_validation_failures_total` (or count 401/403 responses) in alert_rules. For time-skew specifically, add a `node_timex_offset_seconds` metric scrape from node-exporter — if clock drift > 30s, fire `ClockSkewDetected` alert regardless of HTTP metrics. This separates semantic fault detection from HTTP performance detection.

---

### Gap 2: Infrastructure service topology depth misconfiguration (DNS RCA miss)

**Symptom:** Experiment 9 (dns_resolver_slow) detected correctly but RCA picked `api-gateway` instead of `dns-resolver`. MTTD = 52s (slowest for detected experiments).

**Likely cause in pipeline (§7.3):** `dns-resolver` is modeled in `topology.json` as a child of `api-gateway` (api-gateway → dns-resolver edge), giving it depth=2. When DNS is slow, `api-gateway` shows amplified latency (it calls DNS frequently) and fires more alerts than `dns-resolver` itself. The temporal tiebreak then selects `api-gateway` (more alerts, same or earlier fire_ts). The real dependency is inverted: `dns-resolver` is infrastructure that `api-gateway` *depends on*, meaning it should be at depth=0 or classified as an infrastructure root.

**Recommended fix:** Introduce a service classification system in `topology.json`: `"tier": "infra"` vs `"tier": "app"`. RCA should give infrastructure-tier services a synthetic depth of 0, making them always preferred as root when alerting. This matches real-world dependency: infrastructure faults are always upstream causes, application faults are downstream effects.

---

### Gap 3: Slow MTTD for gradual faults (memory, disk, DNS)

**Symptom:** Experiments 5 (memory fill, 47s), 7 (disk fill, 58s), and 9 (DNS slow, 52s) have the highest MTTDs. All involve gradual resource exhaustion rather than instant hard failures.

**Likely cause in pipeline (§7.1):** Alert rules use fixed `for` conditions (20-30s) with `rate()` windows of 1-2 minutes. Gradual faults accumulate slowly — the signal doesn't cross the threshold until late in the fault window. Additionally, `for: 20s` means the alert only fires after the condition is sustained, adding latency on top of the rate window.

**Recommended fix:** Add a second detector tier for gradual faults: short-window trend alerts using `predict_linear()` in Prometheus. Example: `predict_linear(container_memory_usage_bytes[5m], 300) > threshold` fires before the metric crosses the threshold, giving 5-minute early warning. For disk: `predict_linear(disk_usage[10m], 600) > 0.95`. This implements §6.1 percentile-based baselines with forward projection rather than reactive thresholding.

---

## 5. Hypothesis for Unconfirmed Gaps

### Gap 1 extension: Would a JWT-aware mock confirm the auth detection gap?

If the mock service were enhanced to implement real JWT validation (checking `exp` claim against `time.time()`), clock skew experiment would likely produce 401 errors detectable via a 4xx error rate metric. The hypothesis is that adding `status=~'4..'` counting in `http_errors_total` would achieve MTTD < 30s for clock skew. This could be confirmed by: (1) injecting clock skew via `date -s` with a JWT-aware service, (2) observing 401 response codes in Prometheus, (3) verifying that `HighErrorRate` alert fires within 30s. Recommended as follow-up experiment 11.

### Gap 2 extension: Infrastructure tier RCA validation

To confirm the topology tier fix for DNS: add `"tier": "infra"` to `dns-resolver` in `topology.json`, modify RCA to set `infra_tier_depth = 0`, then re-run experiment 9. The hypothesis is RCA will pick `dns-resolver` with confidence ≥ 0.78 instead of `api-gateway`. This is a deterministic fix that can be verified in 1 re-run.
