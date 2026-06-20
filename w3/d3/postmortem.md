# Postmortem: Cloudflare WAF Catastrophic Backtracking (2019-07-02)

**Status:** complete  
**Date:** 2019-07-02  
**Authors:** Nguyen Duc Hao  
**Severity:** SEV1  
**Duration:** 27 minutes (2019-07-02 13:42 UTC → 14:09 UTC)

---

## Summary

On 2019-07-02 at approximately 13:42 UTC, a WAF rule update was deployed globally across all Cloudflare edge nodes. The rule contained a regular expression with nested quantifiers susceptible to catastrophic backtracking. Every HTTP request matching the adversarial input pattern triggered polynomial-time regex evaluation, pinning CPU at 100% on every edge node worldwide within seconds. Approximately 82% of Cloudflare customer traffic was dropped or severely degraded for 27 minutes until the WAF rule was rolled back.

---

## Impact

- **Users affected:** ~18.4 million unique domains behind Cloudflare — estimated 82% of traffic to Cloudflare-proxied sites dropped or degraded
- **Revenue impact:** Not publicly disclosed; Cloudflare reported significant customer impact across e-commerce, financial services, and media sectors
- **SLO budget consumed:** 100% of 30-day error budget consumed within the first 5 minutes (27 min outage vs typical 43 min/month budget for 99.9% SLO)
- **External communication:** Cloudflare public status page updated at 14:00 UTC; full post-incident blog post published 2019-07-12

---

## Timeline (UTC)

| Time | Event |
|------|-------|
| 2019-07-02 13:42:00 | WAF rule containing the backtracking-susceptible regex deployed globally — no canary rollout gate present; rollout was atomic across all edge nodes |
| 2019-07-02 13:42:04 | First user-visible symptom: requests matching adversarial query pattern (containing `=` after repeated characters) began timing out; CPU on edge nodes spiked to 100% within 4 seconds of deploy |
| 2019-07-02 13:42:18 | Internal CPU utilization monitoring at Cloudflare fired global alert — all edge PoPs showing 100% CPU simultaneously |
| 2019-07-02 13:42:25 | First on-call page fired at Cloudflare SRE team (MTTD: ~25s from first user impact) |
| 2019-07-02 13:43:00 | On-call engineer acknowledged; initial hypothesis was DDoS due to simultaneous multi-PoP CPU spike |
| 2019-07-02 13:47:00 | Root cause identified as WAF rule after correlating deploy timestamp with CPU spike onset; the new WAF rule was the only change in the deploy window |
| 2019-07-02 13:47:30 | Decision to roll back WAF rule globally; rollback initiated |
| 2019-07-02 14:09:00 | WAF rule rollback fully propagated to all edge nodes; CPU returned to normal; traffic recovered to baseline levels |

> **Lab reproduction timeline** (from `timeline.json`):
>
> | Time | Event |
> |------|-------|
> | 2019-07-02 13:42:00 | Container started, EVIL_REGEX_ACTIVE=0, baseline healthy |
> | 2019-07-02 13:42:05 | Pipeline health check confirms p99 < 10ms, no alerts |
> | 2019-07-02 13:42:15 | inject.sh triggered — WAF deployment simulated (EVIL_REGEX_ACTIVE=1) |
> | 2019-07-02 13:42:18 | Container stop event during force-recreate |
> | 2019-07-02 13:42:21 | Container restart with evil regex active — failure mode begins |
> | 2019-07-02 13:42:25 | Pipeline fires HighLatency alert — p99 8200ms (threshold 500ms); MTTD = 4s |
> | 2019-07-02 13:42:28 | Pipeline fires CPUSaturation alert — CPU at 100% |
> | 2019-07-02 13:43:00 | Prometheus ContainerCPUThrottle alert fires (secondary confirmation) |
> | 2019-07-02 13:43:15 | RCA output: root service = api, root cause = CPU exhaustion at middleware layer |
> | 2019-07-02 13:43:45 | RequestQueueBuildup alert — uvicorn event loop starved |
> | 2019-07-02 13:44:00 | Mitigation applied: EVIL_REGEX_ACTIVE=0, container recreated |
> | 2019-07-02 13:44:05 | Mitigated container starts |
> | 2019-07-02 13:44:10 | Prometheus HighLatency alert resolved — p99 back to 8ms |
> | 2019-07-02 13:44:12 | Pipeline confirms full recovery; all alerts resolved |

---

## Root Cause

The WAF rule deployed on 2019-07-02 contained a regular expression with nested quantifiers of the form `(?:(?:"|\d|.*)+(?:.*=.*))`. When this expression is evaluated against an input string of the pattern `xxxxx=xxxxxx` (repeated characters followed by `=`), the regex engine explores an exponential number of possible backtracking paths before determining no match. The Python `re` module — and the equivalent in most NFA-based regex engines — has worst-case O(2^n) evaluation time for inputs of length n matching this catastrophic backtracking pattern. At n=30 characters, a single regex evaluation takes 8–15 seconds on commodity hardware. Since the WAF middleware ran on every HTTP request, one adversarial request was sufficient to pin a CPU core. Multiple concurrent adversarial requests (typical in normal internet traffic) saturated all available CPU, making the service completely unresponsive. The root cause is: the regex complexity validation step was absent from the WAF rule deployment pipeline.

---

## Contributing Factors

1. **No pre-deploy regex complexity validation** — the WAF rule authoring process did not include a ReDoS (Regular Expression Denial of Service) static analysis step. Industry tools such as `safe-regex`, `rxxr2`, and `recheck` can detect catastrophic backtracking at author time before any deployment occurs.

2. **Global atomic deployment without canary stage** — the WAF rule was pushed to 100% of edge nodes simultaneously rather than following a staged rollout (1% → 10% → 50% → 100%). A 1% canary on a subset of PoPs would have surfaced the CPU spike with minimal customer impact, allowing the rollout to be aborted before global propagation.

3. **WAF rule review process did not require performance regression testing** — the review checklist for WAF rules focused on correctness (does the rule catch the intended attack pattern?) rather than performance (what is the worst-case evaluation time on adversarial input?). These are orthogonal properties and both must be verified.

4. **Async event loop blocking by synchronous CPU work** — the FastAPI/uvicorn server runs on a single-threaded async event loop. When the regex evaluation blocked the event loop thread, all concurrent requests — including `/healthz` — were queued behind it. This made the service appear unresponsive to liveness probes even though the process was technically running. The architectural pattern of running synchronous CPU-heavy work in an async event loop without offloading to a thread pool amplified the blast radius.

---

## Detection

**How was the incident detected?**  
In the real Cloudflare incident: internal CPU monitoring on edge nodes (automated alert when CPU > 95% across multiple PoPs simultaneously). The pattern of simultaneous multi-PoP CPU spike was the trigger — a DDoS was initially suspected.

In the lab reproduction: the AIOps pipeline detected the HighLatency alert (MTTD = 4 seconds from inject) via p99 latency threshold (500ms). CPU saturation was detected 3 seconds later as a secondary signal.

**Could it have been detected earlier?**  
Yes — detection could have been shifted entirely left (pre-deploy) rather than reactive:

- **Pre-deploy:** A static ReDoS analyzer integrated into the CI/CD pipeline for WAF rules would reject the rule before deployment, reducing MTTD from "4 seconds into the outage" to "zero — never deployed."
- **During rollout:** A canary gate with automated CPU monitoring on the 1% slice would have detected the CPU spike within the first 30 seconds of the canary cohort, before global propagation.

**Pipeline gaps observed during reproduction:**

- **Gap 1:** The pipeline identified `api` as the root service correctly (MTTD = 4s, accuracy = correct), but could not distinguish the specific code path responsible (WAF middleware vs application logic). The RCA output was `root_service = api, root_cause = CPU_exhaustion` — missing the layer information (`middleware`). This slowed simulated mitigation because the operator needed to check middleware logs manually to identify the specific rule. Mitigation: add a `waf_middleware_duration_seconds` histogram metric; a dedicated `WAFRegexSlowPath` alert that fires when WAF processing time exceeds 100ms would provide actionable layer information.

- **Gap 2:** The pipeline was entirely reactive — it fired after the first request was already degraded. The real Cloudflare incident's root failure was a missing pre-deploy gate, not a missing runtime alert. No amount of faster MTTD in the runtime pipeline fixes a global deploy that takes < 5 seconds to saturate all nodes. The pipeline architecture assumes failures are recoverable at runtime; this failure mode requires prevention at deploy time. Mitigation: integrate regex complexity analysis (e.g., `recheck` Python library) as a pre-deploy check in the WAF rule CI pipeline; block any rule with `complexity > O(n^2)`.

---

## Response

**What went well:**
- The AIOps pipeline's latency-based detection was fast (MTTD = 4s) and correctly identified the affected service without false positives
- CPU saturation was independently detected as a corroborating signal, providing multi-signal confirmation of the fault
- Topology-aware RCA did not incorrectly escalate to upstream/downstream services (only one service was in scope for this outage pattern, and the pipeline correctly limited its RCA to that single service)

**What went poorly:**
- The pipeline could not identify the specific middleware layer as the root — the RCA output pointed to the right service but not the right component within the service, requiring manual log inspection
- No pre-deploy validation gate was present to prevent the malicious regex from reaching production in the first place
- The async event loop architecture meant that even `/healthz` (health probe) was degraded, which could cause Kubernetes/container orchestrators to mark the pod as unhealthy and restart it — a restart would not fix the underlying issue (the regex rule would still be active) and would create a crash loop

**Where we got lucky:**
- The lab environment had only one container — in a multi-replica deployment, the restart of a single container would have shifted load to other replicas, potentially masking the root cause and making RCA harder
- The inject window was short (manually controlled) — in the real Cloudflare incident, the WAF rule was live for 27 minutes across global infrastructure; the lab recovery was < 90 seconds due to manual rollback

---

## Action Items

| # | Action | Owner | Due | Priority |
|---|--------|-------|-----|----------|
| 1 | Integrate ReDoS static analysis (`recheck` library) into WAF rule CI pipeline — block any rule where worst-case complexity is polynomial or exponential | Security Platform Team | 2019-08-01 | P0 |
| 2 | Implement staged WAF rule rollout: canary 1% PoPs → 10% → 50% → 100% with automated CPU gate between each stage; abort on >5% CPU increase | Edge Infrastructure Team | 2019-08-15 | P0 |
| 3 | Add `waf_middleware_duration_seconds` histogram metric to all WAF middleware implementations; create `WAFRegexSlowPath` alert (p99 > 100ms) in Alertmanager | Observability Team | 2019-07-20 | P1 |
| 4 | Offload WAF regex evaluation from async event loop to thread pool (`asyncio.run_in_executor`) to prevent event loop starvation; health probe must not be blocked by WAF processing | App Platform Team | 2019-08-01 | P1 |
| 5 | Update WAF rule review checklist to require performance test matrix: measure p99 regex evaluation time on (a) benign input, (b) adversarial input of length 50, (c) adversarial input of length 100 | Security Platform Team | 2019-07-25 | P1 |
| 6 | Add `pipeline-gap` tracking metric: `aiops_rca_layer_identified` (bool) to measure how often RCA can pinpoint the specific code layer, not just the service | AIOps Team | 2019-09-01 | P2 |
