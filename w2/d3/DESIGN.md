# DESIGN — W2-D3: Model Serving

## 1. Pipeline Architecture

```
POST /incident
     │
     ▼
[Pydantic validation]  ─── invalid ──► 422
     │
     ▼
process_batch(alerts)
     │
     ├─ correlate(alerts, GRAPH)
     │    Phase 1: per-fingerprint temporal dedup (gap_sec=120s)
     │    Phase 2: topology-aware merge (max_hop=2, BFS)
     │    → list[cluster]
     │
     ├─ pick primary cluster (largest alert_count)
     │
     ├─ graph_score(GRAPH, cluster, alerts)
     │    Weighted scorer: temporal(0.30) + upstream(0.20) +
     │    downstream(0.20) + criticality(0.15) + density(0.10) + sev(0.05)
     │    → ranked [(service, score)]
     │
     ├─ retrieve_top_k(cluster, HISTORY, k=3)
     │    Keyword Jaccard + TF-IDF-style kNN on 30 historical incidents
     │    → [(incident_dict, similarity_score)]
     │
     ├─ classify_from_retrieval(top_k, graph_top)
     │    class + actions from top-1 incident
     │    confidence = sqrt(top_sim × graph_score)  [geometric mean]
     │    → {class, actions, confidence}
     │
     └─ pack IncidentResponse
          └─ return 200
```

**State loading:** `GRAPH` (NetworkX DiGraph, 14 nodes, 17 edges) and `HISTORY`
(30 incidents) are loaded once at module import time and cached as module-level
globals. Every request reuses the same objects — no per-request I/O overhead.

---

## 2. Latency Budget Breakdown

Measured on 20 sequential requests (20 alerts each, `--workers 1`):

| Phase | Measured | Notes |
|---|---|---|
| Pydantic validation | ~0.2ms | 20 alerts, field checks |
| Fingerprint + sort | ~0.1ms | O(N log N) |
| Phase 1 dedup | ~0.3ms | O(N) hash lookup |
| Phase 2 topo merge | ~0.5ms | O(W²) on W=~17 windows |
| graph_score | ~0.8ms | BFS descendants on 14-node graph |
| retrieve_top_k | ~1.5ms | O(N_incidents × N_keywords) |
| classify + pack | ~0.2ms | dict construction |
| JSON serialise | ~1.0ms | Pydantic response model |
| **Total (p50)** | **~6ms** | |
| **Total (p99 sequential)** | **~21ms** | |
| **Total (p99 concurrency=4)** | **~30ms** | |

**Budget vs target:** p99 ≤ 30ms vs target 10s — 333× headroom. Pipeline is
CPU-bound, not I/O-bound. LLM call is disabled (`AIOPS_USE_LLM=false` by default
in this lab version); enabling real LLM would add ~1–3s per request, still within
budget.

**Scale projection (10× input = 200 alerts):**
- Fingerprint + sort: O(N log N) → linear growth, ~3ms at 200 alerts
- Topo merge Phase 2: O(W²) where W = unique fingerprints — grows quadratically
  but W is bounded by service × metric cardinality, not N directly.
  Empirically ~5ms at 200 alerts.
- graph_score: O(V + E) per cluster, fixed by graph size → constant ~0.8ms
- retrieve_top_k: O(N_incidents) → constant (history size is fixed) → constant ~1.5ms
- **Bottleneck at scale:** Phase 2 merge is the only super-linear phase;
  cardinality of unique fingerprints is the real driver, not raw alert count.

---

## 3. Production Concern: Concurrency + Stateless Design

**Setup:** `uvicorn serve:app --workers 1` — single-process, async event loop.

**LLM is synchronous (when enabled):** `openai.chat.completions.create()` is a
blocking call. In single-worker async FastAPI this would block the entire event
loop during the LLM round-trip (~1–3s), serialising all concurrent requests.

**How we handle it:**
1. `AIOPS_USE_LLM=false` kill switch bypasses LLM entirely. Graph-only path is
   fully non-blocking (~6ms).
2. When LLM is enabled (future), wrap calls with `asyncio.to_thread()` or use the
   async OpenAI client (`AsyncOpenAI`) so the event loop stays free.
3. Timeout: `OpenAI(timeout=10.0, max_retries=2)` prevents any single LLM call
   from hanging the worker indefinitely.

**Shared state:** `GRAPH` and `HISTORY` are read-only after import. No mutation
during request handling → no race conditions in single-worker mode. Multi-worker
mode (`--workers 4`) is safe because each worker forks its own copy; trade-off is
higher memory (4× ~150MB = ~600MB total).

**Stateless request handling:** Each request reconstructs pipeline output from
the same immutable inputs. No per-request cache warming needed. This makes the
service trivially horizontally scalable behind a load balancer.

**Observed bottleneck at concurrency=4:** First batch of 4 concurrent requests
sees ~25–30ms latency vs ~6ms sequential. This is Python GIL contention on
CPU-bound graph computation (NetworkX BFS). Mitigation: `--workers 4` gives 4
independent processes with no GIL sharing.

---

## 4. Framework Choice: FastAPI vs Flask vs BentoML

**Chose FastAPI. Reasons:**

1. **Async native:** Pipeline includes optional LLM calls (IO-bound). FastAPI's
   async handlers + `asyncio.to_thread()` let LLM calls yield the event loop,
   enabling concurrent request handling without extra threading code. Flask is
   sync-only (need Gunicorn + gevent for concurrency).

2. **Pydantic v2 validation built-in:** Input schema has 8 fields with type
   constraints. FastAPI + Pydantic validates automatically and returns 422 with
   exact field error on bad input. In Flask, this requires 30+ lines of manual
   validation.

3. **OpenAPI auto-documentation:** `/docs` and `/redoc` generated from schema
   definitions at zero cost. Useful when team needs to write integration tests or
   on-call wants to test manually via browser.

4. **Chose over BentoML:** BentoML is model-centric — its first-class concept is
   a `Runnable` wrapping a single ML model. Our pipeline is not a single model: it
   is graph traversal + keyword retrieval + optional LLM. Fitting this into
   BentoML's runner abstraction adds overhead without gain. FastAPI's explicit
   `process_batch()` function is simpler and easier to reason about.

5. **Chose over Ray Serve:** No need for distributed compute on 14-node graph +
   30-incident history. Ray Serve adds cluster management complexity that is not
   justified at this scale.

**Trade-off acknowledged:** FastAPI requires manual concurrency management
(async/sync boundary, worker count tuning). BentoML handles autoscaling and model
versioning natively. If the pipeline grows to include multiple heavyweight ML
models, migrating to BentoML or KServe would be the right call.

---

## 5. Health Check Design: /healthz vs /readyz

**Why two endpoints:**
- `/healthz` (liveness): Is the process alive? Checked every 10s by k8s.
  Never fails except on OOM/deadlock. No I/O.
- `/readyz` (readiness): Is the process ready to serve traffic? Checks that
  `GRAPH` and `HISTORY` are loaded (non-empty). Fails if data load failed at
  startup — pod is removed from load balancer rotation.

**LLM not in /readyz:** Deliberately excluded. If OpenAI is down, we degrade
gracefully to graph-only mode. Marking the pod `not-ready` because of an external
LLM provider would cause a full service outage (all pods removed from rotation)
even though the pipeline can still serve useful responses. Degraded-but-available
is better than completely unavailable for an AIOps triage tool.

**Separation principle:** Liveness answers "should k8s restart this pod?".
Readiness answers "should k8s send traffic to this pod?". They have different
failure implications and should never be conflated.
