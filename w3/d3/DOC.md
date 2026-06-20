# DOC.md — W3-D3: Outage Reproduction, Postmortem, ADR, Cost Model

> Tài liệu tham khảo tổng hợp cho bài lab W3-D3. Phục vụ cho việc ôn tập, vấn đáp, và hiểu sâu nội dung.

---

## 1. Mục Đích Bài Lab

### Mục tiêu tổng quát

Bài lab W3-D3 kết thúc vòng học W3 bằng cách yêu cầu học viên **đóng vòng lặp SRE hoàn chỉnh**: từ việc tái hiện một sự cố thực tế (outage reproduction), phân tích sau sự cố (postmortem), ghi lại quyết định kiến trúc (ADR), đến đánh giá kinh tế của nền tảng AIOps (cost model).

### 3 câu hỏi cốt lõi bài lab muốn trả lời

1. **Pipeline của tôi có phát hiện được failure mode này không?** → Qua reproduction + capture_timeline.py + alerts_observed.json
2. **Khi sự cố xảy ra, tôi mô tả nó như thế nào cho đúng chuẩn?** → Qua postmortem.md (Google SRE blameless format)
3. **Platform AIOps có đáng đầu tư không?** → Qua cost_model.py + ADR

### Mục tiêu cụ thể (theo §9.10 Acceptance Checklist)

| Mục tiêu | Deliverable |
|---------|------------|
| Tái hiện outage và capture timeline ≥ 8 events | `reproduction/`, `timeline.json` |
| Quan sát pipeline output trên outage | `alerts_observed.json`, `rca_observed.json` |
| Viết blameless postmortem đầy đủ field | `postmortem.md` |
| Ghi lại quyết định kiến trúc có ≥ 2 alternatives | `ADR.md` |
| Implement cost model đúng schema | `cost_model.py` |
| Tổng hợp toàn bộ W3 vào một spec document | `SPEC.md` |
| Reflection cá nhân về learning | `SUBMIT.md` |

---

## 2. Nội Dung Kiến Thức Cần Nắm

### 2.1 Postmortem

**Định nghĩa:** Document phân tích incident sau khi resolve, bao gồm timeline đầy đủ, root cause, contributing factors, và action items.

**Blameless principle (quan trọng nhất):**
- **Không bao giờ viết:** "Alice pushed bad config" → đây là blame wording
- **Phải viết:** "Config push pipeline allowed invalid YAML through" → systemic cause
- **Lý do:** Blame culture → người sợ báo lỗi → bug ẩn lâu hơn → outage nghiêm trọng hơn
- **Test nhanh:** Tìm tất cả instances của `<tên người> did X`. Nếu có = fail.

**Format Google SRE (7 section bắt buộc):**
```
Summary → Impact → Timeline → Root Cause → Contributing Factors 
→ Detection → Response → Action Items
```

**Timeline quy tắc:**
- Tối thiểu 8 events với UTC timestamp
- Event quan trọng nhất: trigger, first symptom, first alert, ack, root cause identified, mitigation, full recovery
- Dùng `timeline.json` (output của `capture_timeline.py`) làm nguồn

### 2.2 Root Cause Analysis

**5 Whys — dùng khi:**
- Outage có single failure path (linear causation)
- Team mới bắt đầu làm RCA
- Giới hạn: assume tuyến tính, fail khi có nhiều contributing factors

**Causal Tree — dùng khi:**
- Outage có > 1 failure mode đồng thời (Roblox: Consul streaming + BoltDB)
- Có architectural decision contribute (consistency vs availability)
- Vẽ dạng cây, mỗi branch là một independent cause

**5 Whys ví dụ (từ bài lab):**
```
Symptom: API returns 500 → CPU 100%
Why?  → Regex middleware consuming all CPU
Why?  → EVIL regex triggers catastrophic backtracking
Why?  → Nested quantifiers create O(2^n) backtracking paths
Why?  → Regex complexity was not validated before deploy
Why?  → WAF rule CI pipeline lacked ReDoS static analysis step
Root cause: WAF rule deployment pipeline lacked complexity gate
```

### 2.3 Failure Mode Catalog — 6 Patterns

| Pattern | Cơ chế | Ví dụ thực tế |
|---------|--------|---------------|
| Cascading failure | A fail → retry storm → B saturate → C fail | AWS Lambda 2018 |
| Split-brain | Network partition → 2 nodes đều nghĩ mình là primary | GitHub MySQL 2018 |
| **Catastrophic backtracking** | Regex/parser exponential time on adversarial input | **Cloudflare 2019** (bài này) |
| Capacity exhaustion | File descriptor, conn pool, thread pool full | LinkedIn 2017 |
| Monitoring loop | AIOps stack depends on failing service → monitoring blind | Roblox 2021 |
| Operator action | Typo / wrong scope command takes down prod | AWS S3 2017 |

**Detection trap của Cascading failure:** Alert nhiều nhất ở C (downstream), không phải A (root). Naive RCA pick C → sai. Cần topology-aware + causal-lag analysis.

### 2.4 Architecture Decision Record (ADR)

**Format Nygard (chuẩn ngành):**
```markdown
# ADR-NNN: <short title>
## Status: Proposed | Accepted | Deprecated | Superseded by ADR-XXX
## Context: <situation forcing the decision>
## Decision: <the change we're making>
## Alternatives considered: (≥ 2, mỗi cái có pros/cons)
## Consequences: (≥ 1 positive, ≥ 1 negative/trade-off)
```

**Quy tắc viết ADR tốt:**
- Decision phải là active voice, single sentence: "We will use X"
- Alternatives phải thực sự được cân nhắc, không phải straw men
- Consequences phải honest về trade-offs — không chỉ toàn positive
- Reference incident/gap từ postmortem để justify decision

### 2.5 Cost Model — Break-Even Formula

```python
monthly_value = incidents_per_month × avg_duration_h × mttr_reduction_pct × downtime_$/h
roi = monthly_value / aiops_monthly_cost
```

**Verdict rule:**
- `roi > 1.5` → **worth_it**
- `1.0 < roi ≤ 1.5` → **marginal**  
- `roi ≤ 1.0` → **not_worth_it**

**Khi KHÔNG nên làm AIOps (§8.5):**
- < 30 services và < 3 incidents/month
- Downtime cost < $1k/hour (internal tools)
- Observability stack chưa mature (no SLO, no centralized log)
- Postmortem culture chưa establish

### 2.6 Metric quan trọng

| Metric | Công thức | Ý nghĩa |
|--------|-----------|---------|
| MTTR | Avg recovery time | Trung bình thời gian recover |
| MTTD | Avg detection time | Trung bình thời gian phát hiện |
| MTBF | Avg time between failures | Trung bình thời gian giữa các failure |
| Error budget | (1 - SLO) × total_events | Quota cho phép fail trong 30 ngày |
| Burn rate | actual_error_rate / (1 - SLO) | Tốc độ đốt error budget |

---

## 3. Outage Được Chọn: Cloudflare WAF Catastrophic Backtracking (2019-07-02)

### Tóm tắt sự cố

- **Ngày:** 2019-07-02, 13:42 UTC
- **Duration:** 27 phút
- **Failure mode:** Catastrophic backtracking (Pattern #3 trong catalog)
- **Blast radius:** ~82% traffic Cloudflare toàn cầu dropped
- **Root cause:** WAF rule chứa regex `(?:(?:"|\d|.*)+(?:.*=.*))` — nested quantifiers tạo O(2^n) backtracking trên input `xxxxx=xxxxxx`
- **Why chosen:** Medium difficulty, runnable reproduction, pattern điển hình và có thể reproduce chính xác trong lab

### Tại sao pattern này đặc biệt nguy hiểm

```
User input: x=xxxxxxxxxx... (n characters before '=')

Regex engine (NFA-based) tries:
- Match .* as 0 chars, (\d|.*) as n chars, then check .*=.*
- Match .* as 1 char, (\d|.*) as n-1 chars, then check .*=.*
- Match .* as 2 chars, (\d|.*) as n-2 chars, then check .*=.*
- ... 2^n combinations total
```

Khi `n=30` → 2^30 ≈ 1 tỷ operations → một request mất 8-15 giây trên commodity hardware.

### Lý do Cloudflare không có canary

Vào 2019, Cloudflare deploy WAF rules atomic global thay vì staged rollout. 100% edge nodes nhận rule cùng lúc → zero canary buffer. Sau sự cố, Cloudflare implement staged WAF deploy: 1% → 10% → 100% với CPU gate giữa mỗi stage.

---

## 4. Công Nghệ Áp Dụng và Triển Khai

### 4.1 Reproduction Stack

```
reproduction_templates/cloudflare_regex_2019/
├── docker-compose.yml    # FastAPI service on :8888
├── inject.sh             # Flip EVIL_REGEX_ACTIVE=1, force-recreate container
└── app/
    └── main.py           # FastAPI + WAF middleware với evil regex
```

**Core code (main.py):**
```python
EVIL = re.compile(r'(?:(?:"|\d|.*)+(?:.*=.*))')

@app.middleware("http")
async def waf(request: Request, call_next):
    if os.environ.get("EVIL_REGEX_ACTIVE") == "1":
        EVIL.match(str(request.url.query))  # ← blocks event loop!
    return await call_next(request)
```

**Cách trigger:**
```bash
docker compose up -d
# Baseline: curl http://localhost:8888/healthz → < 10ms
bash inject.sh
# Evil regex active: every request triggers backtracking
time curl "http://localhost:8888/?q=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx="
# Expected: 8-15 seconds (CPU pinned)
```

### 4.2 Pipeline Integration

```
capture_timeline.py  →  timeline.json  (≥ 8 events, UTC ISO8601)
                     →  alerts_observed.json (từ /alerts endpoint)
                     →  rca_observed.json   (từ /rca endpoint)
```

**capture_timeline.py** thu thập từ 3 nguồn:
1. `docker events` — container lifecycle (start/stop/restart)
2. `Prometheus /api/v1/alerts` — active alerts tại thời điểm capture
3. `AIOps pipeline /alerts` — pipeline alerts với timestamp

### 4.3 Tech Stack Toàn Lab

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Metrics | Prometheus v2.51 | Time-series scrape từ /metrics endpoint |
| Alerting | Alertmanager v0.27 | Alert routing + dedup + grouping |
| Dashboards | Grafana v11.2 | Visualization |
| Detection | Isolation Forest + 3σ + LSTM-AE | Anomaly detection ensemble |
| Log parsing | Drain parser | Template extraction từ raw logs |
| Correlation | Graph community detection | Alert clustering |
| RCA | Topology-aware + Granger causality | Root cause identification |
| API | FastAPI (Python 3.11) | Pipeline HTTP endpoint |
| Reproduction | Docker Compose | Isolated failure mode simulation |
| Static analysis | `recheck` library | ReDoS complexity detection |

---

## 5. Pipeline Bài Lab và Những Tối Ưu

### 5.1 Pipeline Flow Tổng Quan

```
[Docker Services]          [Metrics Layer]           [AIOps Pipeline]
     │                          │                          │
     ├─ /metrics ──────────► Prometheus ◄──── scrape ─────┤
     │                          │                          │
     └─ logs ───────────────►  Loki/JSONL ──────────────► Detector
                                │                          │
                                └─ docker events ────────► Correlator
                                                           │
                                                           └─► RCA Engine
                                                                │
                                                         /alerts, /rca
```

### 5.2 Kết Quả Quan Sát (Cloudflare Reproduction)

**Điều pipeline làm đúng:**
- MTTD = 4 giây từ inject (HighLatency alert)
- Root service = `api` — đúng
- Multi-signal: HighLatency + CPUSaturation + ContainerCPUThrottle = 3 independent confirmations
- Topology-aware RCA không escalate sai sang upstream/downstream (chỉ có 1 service trong scope)

**Điều pipeline không làm được (2 gaps):**

| Gap | Mô tả | Impact |
|-----|-------|--------|
| Gap 1 — Layer blindness | Biết `api` là root service, không biết `WAF middleware` là root component trong service | Operator phải xem log thủ công để tìm đúng rule cần rollback |
| Gap 2 — Reactive only | Alert fire AFTER requests degraded. Pre-deploy validation hoàn toàn vắng mặt | 28M failed requests trước khi first alert ở Cloudflare scale |

### 5.3 Tối Ưu Đề Xuất (Từ ADR-008 và Postmortem)

**Tối ưu 1 — Pre-deploy regex complexity gate** (Priority: P0)
```python
# Tích hợp vào CI pipeline của WAF rule
from recheck import analyze
result = analyze(regex_pattern)
if result.complexity_class in ["polynomial", "exponential"]:
    raise DeployBlockedError(f"ReDoS risk: {result.example_adversarial_input}")
```
Effect: Loại bỏ hoàn toàn catastrophic backtracking failure class.

**Tối ưu 2 — Thread pool offload cho CPU-intensive middleware** (Priority: P1)
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=4)

@app.middleware("http")
async def waf(request: Request, call_next):
    if os.environ.get("EVIL_REGEX_ACTIVE") == "1":
        # Offload blocking work — event loop không bị starved
        await asyncio.get_event_loop().run_in_executor(
            _executor, EVIL.match, str(request.url.query)
        )
    return await call_next(request)
```
Effect: `/healthz` và other requests không bị blocked. Blast radius limited.

**Tối ưu 3 — WAF middleware latency metric** (Priority: P1)
```python
# Thêm histogram metric — pipeline có thể phân biệt WAF slow vs app slow
import time
from prometheus_client import Histogram

waf_duration = Histogram("waf_middleware_duration_seconds", "WAF processing time",
                          buckets=[0.001, 0.01, 0.1, 0.5, 1.0, 5.0, 10.0])

@app.middleware("http")
async def waf(request: Request, call_next):
    t0 = time.time()
    if os.environ.get("EVIL_REGEX_ACTIVE") == "1":
        EVIL.match(str(request.url.query))
    waf_duration.observe(time.time() - t0)
    return await call_next(request)
```
Effect: Pipeline có thể fire `WAFRegexSlowPath` alert khi WAF p99 > 100ms — layer-specific detection.

**Tối ưu 4 — Deploy correlation signal** (Priority: P2)
```python
# Trong correlator: nếu nhiều services fire alerts trong 60s
# và có deployment event trong cùng window → add "recent_deploy" hypothesis
def check_deploy_correlation(cluster: AlertCluster, deploy_log: list[DeployEvent]) -> str | None:
    cluster_start = min(a.fire_ts for a in cluster.alerts)
    recent_deploys = [d for d in deploy_log if cluster_start - 60 <= d.ts <= cluster_start + 30]
    if recent_deploys and len(cluster.services) > 2:
        return f"HYPOTHESIS: deploy={recent_deploys[0].version} may have caused {len(cluster.services)} services to degrade simultaneously"
    return None
```
Effect: Khi global deploy causes global outage, pipeline gợi ý "recent deploy" thay vì chỉ nói "tất cả services bị ảnh hưởng."

---

## 6. Deliverables Tổng Hợp

| File | Mô tả | Key requirement |
|------|-------|----------------|
| `reproduction/` | cloudflare_regex_2019 docker-compose + inject.sh | Chạy được, inject trigger CPU pin |
| `timeline.json` | 15 events với UTC ISO8601 timestamp | ≥ 8 events (requirement: ≥ 8) |
| `alerts_observed.json` | 4 alerts: HighLatency, CPUSaturation, CPUThrottle, RequestQueueBuildup | MTTD documented per alert |
| `rca_observed.json` | RCA output: root=api, confidence=0.91, 2 gaps identified | Gap 1 và Gap 2 documented |
| `postmortem.md` | Blameless, 8 sections đầy đủ, 8 events timeline, 6 action items | 0 blame wording, ≥ 2 gaps |
| `ADR.md` (ADR-008) | Pre-deploy regex complexity gate, 3 alternatives, consequences | ≥ 2 alternatives với pros/cons |
| `cost_model.py` | is_worth_it() + 3 scenarios (fintech scenario là custom) | Runs clean, correct schema |
| `SPEC.md` | 7 sections tổng hợp W3 D1+D2+D3 | Tất cả sections có content |
| `SUBMIT.md` | Reflection 5 sections | Self-check checklist |
| `DOC.md` | Tài liệu này | Phục vụ vấn đáp |

---

## 7. Câu Hỏi Vấn Đáp Thường Gặp

### Q1: Tại sao chọn Cloudflare 2019 thay vì AWS S3 2017?

AWS S3 2017 (operator typo) là pattern `operator_action` — failure mode đơn giản và prevention rõ ràng (blast radius limit, dry-run confirmation). Cloudflare 2019 (catastrophic backtracking) thú vị hơn vì:
1. Service không crash — nó "alive but unresponsive" → harder to detect
2. Pipeline detect được (MTTD = 4s) nhưng không thể prevent ở runtime
3. Buộc phải ra quyết định kiến trúc về "shift left" — pre-deploy validation vs runtime detection
4. Pattern liên quan trực tiếp đến AIOps context: WAF rule là dạng "pattern" tương tự alert rules và log parsing rules

### Q2: Tại sao MTTD = 4 giây là "too late" trong context này?

Cloudflare xử lý ~7 triệu requests/giây qua 200+ PoPs. Trong 4 giây đầu:
- 7M × 4 = 28 triệu requests bị failed hoặc bị queue vô thời hạn
- Tất cả 200+ PoPs đều bị hit cùng lúc (atomic deploy)
- Không có healthy PoP nào để route traffic sang

Trong contrast: một microservice bình thường serve ~1000 req/s. MTTD = 4s × 1000 = 4000 failed requests — manageable. Scale changes everything.

### Q3: Sự khác biệt giữa Gap 1 và Gap 2 là gì?

- **Gap 1 (Layer blindness):** Pipeline biết *đâu* bị hỏng (service `api`), không biết *gì* bị hỏng (WAF middleware layer). Đây là vấn đề về **observability resolution** — cần thêm per-component metrics.
- **Gap 2 (Reactive only):** Pipeline chỉ detect *sau khi* fault đã manifest. Đây là vấn đề về **defense posture** — cần shift from reactive detection sang preventive validation.

Gap 1 = đúng service, sai layer. Gap 2 = đúng signal, sai timing.

### Q4: ADR-008 có rủi ro gì?

- **False rejects:** Một số regexes có worst-case polynomial nhưng không bao giờ trigger trên production inputs (adversarial input cần > 10,000 chars). Hard-reject có thể block valid security rules.
- **Workaround culture:** Nếu tỷ lệ false reject cao, security team sẽ habitually dùng `SECURITY_OVERRIDE` → cơ chế mất ý nghĩa.
- **Mitigation:** Tune threshold bằng `max_evaluation_ms_at_n50` (thực nghiệm) thay vì chỉ dùng theoretical worst-case class. Empirical benchmark ít false positive hơn static analysis thuần túy.

### Q5: Cost model có limitation gì?

1. **Single value metric:** Chỉ tính MTTR reduction value. Không tính: alert fatigue reduction, compliance value (PCI DSS audit trail), on-call burnout reduction, faster feature shipping (more confidence in deployment).
2. **Fixed 40% MTTR reduction:** Con số này từ industry benchmark, không phải từ actual measurement của stack cụ thể. Real reduction có thể 20% (nếu incidents phức tạp) hoặc 60% (nếu incidents pattern-able).
3. **No compounding effects:** Khi có nhiều AIOps capabilities (W1 + W2 + W3), value compounds — một pipeline hoàn chỉnh tốt hơn tổng các parts.

### Q6: Blameless wording — test nhanh?

Tìm trong postmortem những pattern sau và đổi sang systemic language:
| Có blame | Blameless |
|----------|-----------|
| "Engineer X pushed bad config" | "Config validation pipeline allowed invalid config to be pushed" |
| "On-call was slow to respond" | "Alert routing did not reach on-call's primary device within SLA" |
| "Developer forgot to add test" | "Pre-merge test suite did not cover this scenario" |
| "Operator mistyped command" | "CLI command accepted under-specified scope without confirmation prompt" |

Rule của thumb: nếu bỏ tên người ra, câu vẫn còn meaningful → blameless. Nếu câu mất nghĩa khi bỏ tên người → rewrite.

---

## 8. Tóm Tắt Key Takeaways

1. **Postmortem = system autopsy, not people autopsy.** Focus vào process/tooling gap, không phải ai đã làm gì.

2. **Failure modes có defense strategies khác nhau:** Cascading → circuit breaker; Split-brain → Raft/Paxos; Catastrophic backtracking → static analysis pre-deploy; Capacity exhaustion → resource limit + alerting; Monitoring loop → independent watchdog.

3. **AIOps là necessary nhưng không sufficient.** Một số failure classes (global atomic deploy, semantic auth fault) cần pre-deploy gates và application-level metrics — runtime detection là backstop, không phải first line of defense.

4. **Cost model phải honest về uncertainty.** ROI 1.8x với $30k/hr assumption. Nếu assumption sai 2×, verdict có thể đổi. Luôn sensitivity-test cost model với worst-case và best-case assumptions.

5. **ADR là institutional memory.** Mỗi quyết định kiến trúc không có ADR là technical debt của organization — khi người biết lý do rời đi, quyết định sẽ bị reverse không có lý do chính đáng.
