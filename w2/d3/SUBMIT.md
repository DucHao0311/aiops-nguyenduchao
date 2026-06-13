# SUBMIT — W2-D3: Model Serving

## Checklist

- [x] `serve.py` chạy với `uvicorn serve:app --port 8000 --workers 1`
- [x] `GET /healthz` → `{"status": "ok"}`
- [x] `POST /incident` với valid input → 200, body có `clusters`, `root_cause`, `recommended_actions`
- [x] Invalid input → 422, không 500
- [x] `DESIGN.md` ≥ 100 từ, có concrete decision
- [x] Tests: 17/17 passed (`pytest tests/ -v`)
- [x] `Dockerfile` (bonus)
- [x] `Makefile` (bonus)

---

## EOD Checkpoint — 3 câu hỏi

### 1. Latency thực của endpoint ra sao?

**Measurement:** 20 sequential requests, 20 real alerts mỗi request, đo từ header `X-Response-Time-Ms`.

```
p50 = 6.18ms
p99 = 21.25ms
min = 5.43ms
max = 21.25ms
```

**Phase breakdown (estimate từ profiling):**

| Phase | Thời gian | Scale với input |
|---|---|---|
| Pydantic validate | ~0.2ms | Linear (O(N)) |
| Fingerprint + sort | ~0.1ms | O(N log N) |
| Phase 1 dedup | ~0.3ms | O(N) |
| Phase 2 topo merge | ~0.5ms | O(W²) — W = unique fingerprints |
| graph_score | ~0.8ms | **Fixed** (graph size constant) |
| retrieve_top_k | ~1.5ms | **Fixed** (history size constant) |
| classify + pack | ~0.2ms | Fixed |
| JSON serialise | ~1.0ms | O(N_clusters) |

**Phase chiếm nhiều nhất:** `retrieve_top_k` (~1.5ms) và JSON serialise (~1ms).

**Scale khi input ×10 (200 alerts):**
- `correlate` Phase 2 là phase duy nhất grow super-linear — O(W²) trên số unique fingerprints. Với 200 alerts của ~14 services × 5 metrics = ~70 unique fingerprints → W=70 → O(4900) comparisons vs O(289) hiện tại. Ước tính ~5–8ms ở 200 alerts.
- `graph_score`, `retrieve_top_k`, `classify` là **fixed cost** — không đổi dù input tăng vì chúng operate trên graph (14 nodes) và history (30 incidents), không phải trên raw alerts.
- p99 ước tính ở 200 alerts: ~15–25ms, vẫn trong budget.

---

### 2. LLM provider down hoặc 4 request đồng thời — endpoint handle ra sao?

**Concurrency test (Windows — `concurrent.futures.ThreadPoolExecutor`):**
```
python -c "
import json, concurrent.futures, urllib.request

with open('../d1/dataset/alerts_sample.jsonl') as f:
    alerts = [json.loads(l) for l in f if l.strip()]
payload = json.dumps({'alerts': alerts}).encode()

def call(i):
    req = urllib.request.Request('http://localhost:8000/incident',
        data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return i, r.headers.get('X-Response-Time-Ms'), r.status

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    for i, ms, st in ex.map(call, range(20)):
        print(f'req {i}: {ms}ms status={st}')
"
```

**Kết quả đo được:**
```
Concurrency=4, N=20: p50=13.3ms  p99=29.6ms  errors=0
```

**Bottleneck đầu tiên:** Python GIL — 4 threads cùng chạy NetworkX BFS (CPU-bound)
tranh nhau GIL. Biểu hiện: latency tăng từ 6ms → 13–30ms dưới concurrency=4,
nhưng không có error. Giải pháp production: `--workers 4` (4 independent processes,
không có GIL sharing).

**Fallback path:**
- `AIOPS_USE_LLM=false` env var: bypass LLM hoàn toàn → service vẫn chạy với
  graph-only output khi LLM provider outage. Không cần redeploy code.
- Timeout guard: `OpenAI(timeout=10.0, max_retries=2)` — LLM call không bao giờ
  hang vô hạn; sau 10s sẽ raise exception → endpoint trả 500 với message ngắn,
  không leak stack trace ra client.
- `/healthz` luôn pass dù LLM down.
- `/readyz` cũng pass (LLM không được check trong readyz — xem DESIGN.md §5).

---

### 3. /healthz và /readyz check gì? Vì sao tách 2 endpoint?

**`/healthz` (liveness) checks:**
- Không check gì ngoài process còn sống.
- Return `{"status": "ok"}` ngay lập tức.
- Failure condition: chỉ khi process crash/OOM/deadlock.

**`/readyz` (readiness) checks:**
```python
checks = {
    "graph":   GRAPH.number_of_nodes() > 0,   # 14 nodes expected
    "history": len(HISTORY) > 0,              # 30 incidents expected
}
```
- Fail → 503 nếu graph hoặc history chưa load (startup race condition).
- Pass ngay sau import, vì `GRAPH` và `HISTORY` load synchronously ở module level.

**Vì sao tách 2 thay vì gộp 1:**

Hai endpoint có failure semantics khác nhau:
- Nếu `/healthz` fail → k8s **restart** pod (có thể fix OOM).
- Nếu `/readyz` fail → k8s **remove pod khỏi load balancer** nhưng không restart.

Nếu gộp 1 endpoint: khi data load chậm (startup), pod bị restart thay vì chỉ
cần đợi — restart storm. Tách ra cho phép pod startup gracefully mà không bị
hammered.

**Khi LLM API down, `/readyz` của mình fail hay pass?**

**Pass.** LLM availability không được check trong `/readyz`. Lý do:
- Service có fallback graph-only mode → vẫn serve được request có ích.
- Nếu `/readyz` depend vào OpenAI: khi OpenAI có outage toàn cầu, tất cả pods
  mark `not-ready` → service **down hoàn toàn** dù pipeline vẫn có thể chạy.
- AIOps pipeline phục vụ triage on-call — "degraded output" tốt hơn "no output"
  khi incident đang xảy ra.

---

## Bonus Tests — 17/17 Passed

```
======================== test session starts ========================
platform win32 -- Python 3.11.4, pytest-9.0.3

tests/test_serve.py::test_healthz_200                   PASSED
tests/test_serve.py::test_readyz_200                    PASSED
tests/test_serve.py::test_version_keys                  PASSED
tests/test_serve.py::test_incident_empty_alerts_400     PASSED
tests/test_serve.py::test_incident_invalid_severity_422 PASSED
tests/test_serve.py::test_incident_missing_field_422    PASSED
tests/test_serve.py::test_incident_missing_body_422     PASSED
tests/test_serve.py::test_incident_single_alert_200     PASSED
tests/test_serve.py::test_incident_full_dataset_200     PASSED
tests/test_serve.py::test_incident_root_cause_fields    PASSED
tests/test_serve.py::test_incident_cluster_shape        PASSED
tests/test_serve.py::test_incident_processing_ms        PASSED
tests/test_serve.py::test_x_response_time_header        PASSED
tests/test_serve.py::test_fingerprint_excludes_ts_and_value PASSED
tests/test_serve.py::test_fingerprint_differs_on_metric PASSED
tests/test_serve.py::test_correlate_reduces_20_alerts   PASSED
tests/test_serve.py::test_correlate_cluster_schema      PASSED

======================== 17 passed in 2.75s ========================
```

**Test coverage:**
- Liveness / Readiness / Version endpoints
- Pydantic validation: empty list → 400, invalid severity → 422, missing field → 422
- Happy path: single alert, full 20-alert dataset
- Response schema: root_cause keys, cluster keys, processing_ms, X-Response-Time-Ms header
- Unit tests (no network): `make_fingerprint` dedup invariant, `correlate` output shape

**LLM mock:** Không cần mock trong test suite vì `AIOPS_USE_LLM` default là
`false` trong lab và pipeline không có live LLM call. Nếu bật LLM thật, sẽ
dùng `unittest.mock.patch` để mock `openai.chat.completions.create`.

---

## Reflection

Điểm học được hôm nay: chênh lệch giữa notebook và production không phải ở
thuật toán — mà ở 3 thứ: concurrency (GIL contention dưới 4 threads), failure
handling (LLM timeout phải explicit, không thể dùng default), và observability
(nếu không có X-Response-Time-Ms header, không biết p50/p99 thực sự là bao nhiêu).

`process_batch()` là ~30 dòng trong notebook → ~200 dòng production-ready code
sau khi thêm: error handling, schema validation, logging, metrics, timeout,
kill switch, readiness check. Đây là true cost của "serving".

Gap giữa `graph_score()` notebook và production: hàm gốc depend ngầm vào biến
module-level `alerts`. Production version phải nhận `alerts` as explicit
parameter để testable và re-entrant (quan trọng khi có concurrent requests).
