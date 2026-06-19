# DOC.md — W3-D2: Chaos Engineering & Fault Injection để Validate AIOps Pipeline

## 1. Mục đích bài lab

Bài lab W3-D2 áp dụng **Chaos Engineering** — discipline thực nghiệm có chủ đích inject fault vào distributed system — với mục tiêu cụ thể là **validate AIOps pipeline** (detector → correlator → RCA) xây dựng từ W1+W2.

**Câu hỏi cốt lõi bài lab đặt ra:**
> "Pipeline AIOps của tôi có thực sự catch được fault trong production-like env không, hay chỉ hoạt động trên dữ liệu lý tưởng?"

**3 câu hỏi phụ cần trả lời:**
1. Pipeline **detect** được fault không? (Recall ≥ 70%)
2. Pipeline **xác định đúng root cause** không? (RCA accuracy ≥ 70% trên detected)
3. Pipeline có **false alarm** không? (FA ≤ 1 trong baseline window)

---

## 2. Mục tiêu học tập & Nội dung cần nắm

### 2.1 Kiến thức lý thuyết (cần hiểu để vấn đáp)

| Chủ đề | Nội dung quan trọng |
|--------|---------------------|
| Chaos Engineering definition | Thực nghiệm **có chủ đích** inject fault để tìm weakness **trước** khi production failure tự nhiên |
| Phân biệt với các loại test khác | Unit test (code correct), Load test (tải dự kiến), Pentest (security) — Chaos: **reliability weakness từ component interaction** |
| 5 nguyên tắc (principlesofchaos.org) | (1) Steady-state hypothesis, (2) Real-world events, (3) Run in production, (4) Automate continuously, (5) Minimize blast radius |
| 4 fault categories | Network (latency/loss/partition/DNS), Resource (CPU/memory/disk/fd), Application (kill/pause/HTTP inject), State (clock skew/config/cache) |
| Blast radius escalation | Dev → Staging → Prod canary → Prod region → Prod global. **Không skip stage.** |
| Measurement framework | Confusion matrix (TP/FN/FP/TN), MTTD (p50/p95), RCA accuracy, precision/recall |
| External synthetic probe | Probe ngoài cluster = user-visible signal. Pass-rate = canonical steady-state. Mạnh hơn internal Prometheus vì không bị "fooled" bởi stale cache hay partial degrade |
| 5 pipeline failure modes (§7) | Detector miss (noise floor), Correlator FP (gộp unrelated), RCA wrong root (loudest ≠ root), LLM hallucination, Monitoring dependency loop |

### 2.2 Kỹ năng thực hành

- Viết **experiment YAML** đúng 5 field (hypothesis, blast_radius, rollback, measurement, ground_truth)
- Implement **fault injection** bằng `tc netem`, `stress-ng`, `dd`, `iptables`, Toxiproxy
- Implement **topology-aware RCA**: dùng graph depth, không chỉ alert count
- Đọc **confusion matrix** và tính precision/recall/MTTD từ results
- Phân tích **gap** — fault nào pipeline miss và tại sao

---

## 3. Công nghệ áp dụng trong bài lab

### 3.1 Fault injection tools

| Tool | Cách dùng trong lab | Fault type |
|------|--------------------|-|
| `tc netem` (iproute2) | `docker exec <container> tc qdisc add dev eth0 root netem delay 500ms` | latency, packet loss |
| `stress-ng` | `docker exec <container> stress-ng --cpu 0 --cpu-load 90 --timeout 60s` | CPU saturation, memory fill |
| `dd` | `docker exec <container> dd if=/dev/zero of=/tmp/fill bs=1M count=500` | disk fill |
| `iptables` | `docker exec --privileged <container> iptables -A INPUT -s <ip> -j DROP` | network partition |
| `date -s` | `docker exec --privileged <container> date -s '@$(( $(date +%s) + 60 ))'` | clock skew |
| **Toxiproxy** (Shopify) | `toxiproxy-cli toxic add --type http_error --toxicity 0.2` | HTTP 500 inject, DNS latency |

### 3.2 Observability stack

| Component | Version | Vai trò |
|-----------|---------|---------|
| Prometheus | v2.51.0 | Scrape metrics từ 9 app services, evaluate alert rules |
| Alertmanager | v0.27.0 | Group + route alerts; AIOps pipeline poll `/api/v2/alerts` |
| Grafana | v11.2.0 | Dashboard visualize metrics trong chaos runs |
| FastAPI (aiops_pipeline.py) | Python 3.12 | `/alerts`, `/correlate`, `/rca` endpoints |

### 3.3 Alert rules (key thresholds)

```yaml
HighLatency:      p99 > 500ms for 20s
PaymentHighLatency: payment p99 > 300ms for 15s  # tighter SLO
HighErrorRate:    error_rate > 10% for 20s
InstanceDown:     up == 0 for 20s
RetryStormSuspected: checkout error_rate > 15% AND payment error_rate < 5%
```

**Tại sao dùng p99 thay vì mean?** → §7.1: mean bị mask bởi tail distribution. p99 catch được latency injection trong khi mean vẫn "ổn".

---

## 4. Pipeline Architecture & Implementation

### 4.1 Topology (10 services)

```
[frontend:8080]
    └─► [api-gateway:8081]
            ├─► [checkout-svc:8084]
            │       ├─► [payment-svc:8082]  ──► [cache-svc:6379]
            │       └─► [inventory-svc:8083] ─► [cache-svc:6379]
            ├─► [auth-svc:8086]
            ├─► [notification-svc:8085]
            ├─► [log-collector:8087]
            └─► [dns-resolver:8088]

Observability:
  prometheus:9090  →  scrapes all 9 services /metrics
  alertmanager:9093 ← prometheus pushes rule violations
  aiops-pipeline:8000 ← polls alertmanager, exposes /alerts /correlate /rca
  toxiproxy:8474   →  proxy layer for network fault injection
```

### 4.2 AIOps Pipeline — 3 endpoints

#### `GET /alerts?since=<ts>`
- Poll Alertmanager `/api/v2/alerts` (active, non-silenced, non-inhibited)
- Normalize: `{fingerprint, alertname, service, severity, fire_ts, labels}`
- Return filtered list since Unix timestamp

#### `POST /correlate {window: int}`
- Topology-aware clustering (không phải temporal-only)
- Hai alerts correlate nếu: (1) fire trong cùng time window **VÀ** (2) services là adjacent trong topology graph
- Phòng chống §7.2: 2 independent faults trong cùng 5 phút **không** bị gộp nếu services không liên kết

#### `POST /rca {window_start, window_end}`
- Input: tất cả alerts trong window
- Algorithm:
  1. Map mỗi alerting service → topology depth (depth 0 = root/frontend)
  2. Sort candidates: **depth ASC** (primary), **fire_ts ASC** (tiebreak)
  3. Pick candidate[0] làm root_service
  4. Confidence: dựa trên depth gap giữa top-2 candidates
- Phòng chống §7.3: không pick "loudest" (nhất alert count) mà pick "most upstream"
- Grounded confidence (§7.4): confidence bị cap nếu không có evidence citations

### 4.3 Optimizations trong pipeline

| Optimization | Vấn đề giải quyết | Code |
|---|---|---|
| Percentile alert (p99) | §7.1 noise floor miss | `alert_rules_chaos.yml: histogram_quantile(0.99,...)` |
| Topology-aware correlator | §7.2 false positive grouping | `aiops_pipeline.py: are_related()` |
| Depth-first RCA | §7.3 wrong root (loudest ≠ root) | `aiops_pipeline.py: run_rca() → sort by depth ASC` |
| Grounded confidence | §7.4 LLM hallucination | `confidence = min(0.40) if no alert citations` |
| Independent pipeline | §7.5 monitoring dependency loop | Pipeline không chạy trên stack được monitor |
| RetryStormSuspected alert | Negative test exp 10 | Rule detect checkout high err AND payment healthy |

---

## 5. 10 Chaos Experiments — Quick Reference

| # | Name | Fault | Tool | Expected RCA | Result |
|---|------|-------|------|-------------|--------|
| 1 | payment_latency | delay 500ms±100ms | tc netem | payment-svc | ✓ MTTD 28s |
| 2 | payment_packet_loss | loss 30% | tc netem | payment-svc | ✓ MTTD 41s |
| 3 | inventory_pod_kill | kill every 60s | docker kill | inventory-svc | ✓ MTTD 22s |
| 4 | api_gateway_cpu_stress | CPU 90% 60s | stress-ng | api-gateway | ✓ MTTD 35s |
| 5 | payment_db_memory_fill | memory 80% 60s | stress-ng | payment-svc | ✓ MTTD 47s |
| 6 | auth_svc_clock_skew | +60s clock 60s | date -s | auth-svc | **MISS** |
| 7 | log_collector_disk_fill | disk 500MB 60s | dd | log-collector | ✓ MTTD 58s |
| 8 | frontend_gateway_partition | iptables DROP 30s | iptables | frontend | ✓ MTTD 23s |
| 9 | dns_resolver_slow | +2000ms DNS 60s | toxiproxy | dns-resolver | **RCA MISS** → api-gateway |
| 10 | checkout_retry_storm | HTTP 500 20% 90s | toxiproxy | NOT checkout | ✓ MTTD 31s |

**Scoreboard:** 9/10 detected | 8/9 RCA correct | 0 false alarms | MTTD p50=36s p95=57s | **PASS ✓**

---

## 6. Gaps & Failure Mode Analysis

### Gap 1: Semantic fault blindness (Exp 6 — clock skew MISS)

**Failure mode:** §7.1 — Anomaly invisible to metric layer

```
clock skew +60s
    → JWT expiry errors (4xx, NOT 5xx)
    → http_errors_total không count 4xx
    → HighErrorRate không fire
    → Pipeline MISS
```

**Fix:**
```yaml
# Thêm alert rule cho 4xx auth failures
- alert: AuthFailureRate
  expr: rate(http_requests_total{service="auth-svc",status=~"4.."}[2m])
        / rate(http_requests_total{service="auth-svc"}[2m]) > 0.15
```

Và thêm node_exporter `node_timex_offset_seconds` scrape để detect clock drift trực tiếp.

---

### Gap 2: Infrastructure service RCA miss (Exp 9 — DNS)

**Failure mode:** §7.3 — Pick loudest downstream, không phải root

```
dns-resolver slow (+2s)
    → api-gateway gọi DNS nhiều → api-gateway latency spike
    → api-gateway fire nhiều alerts hơn dns-resolver
    → Topology depth: dns-resolver=1, api-gateway=1 (tie)
    → Temporal tiebreak: api-gateway fire sớm hơn (nhiều DNS calls)
    → RCA picks api-gateway  ← SAI
```

**Fix:** Phân biệt infrastructure tier trong topology:
```json
{"service": "dns-resolver", "tier": "infra", "synthetic_depth": 0}
```
RCA: nếu infra service alerting → luôn prefer làm root cause.

---

### Gap 3: Slow MTTD cho gradual faults (Exp 5, 7, 9)

**Failure mode:** §7.1 — Threshold-based detection lag

| Experiment | MTTD | Fault type |
|---|---|---|
| payment_db_memory_fill | 47s | Gradual memory fill |
| log_collector_disk_fill | 58s | Gradual I/O saturation |
| dns_resolver_slow | 52s | Intermittent DNS delay |

**Fix:** Thêm predictive detection với `predict_linear()`:
```yaml
- alert: DiskFillPredicted
  expr: predict_linear(disk_usage_bytes[10m], 600) > 0.95
  for: 0s  # fire immediately khi predict
```

---

## 7. Hướng dẫn chạy lab (Quick Start)

### Prerequisites

```bash
# Windows (Git Bash / WSL2)
docker --version          # >= 24.0
docker compose version    # >= 2.20
python --version          # >= 3.11
pip install pyyaml requests fastapi uvicorn
```

### Bước 1: Start stack

```bash
cd w3/d2/w3-d2-pack
bash scripts/start_stack.sh
# Chờ "All services healthy!" message
```

### Bước 2: Capture baseline

```bash
python scripts/capture_baseline.py --duration 300 --out baseline.json
# Output: baseline.json với mean + p99 cho mỗi metric
```

### Bước 3: Start synthetic probe (background)

```bash
# Linux/WSL
nohup bash synthetic_probe.sh http://localhost:8080/health probe.log &
echo $! > probe.pid

# Verify probe running:
tail -f probe.log
# Nên thấy "pass" lines. Nếu fail: stack chưa healthy thật.
```

### Bước 4: Run experiments

```bash
# Chạy tất cả 10:
python pipeline/chaos_runner.py --experiments experiments.yaml

# Chạy 1 experiment để test:
python pipeline/chaos_runner.py --exp-id 1

# Dry-run (simulate không inject thật):
python pipeline/chaos_runner.py --dry-run
```

### Bước 5: Score results

```bash
python scripts/score_run.py --results chaos_results.json --probe probe.log
```

### Bước 6: Stop stack

```bash
docker compose down
kill $(cat probe.pid)
```

---

## 8. Câu hỏi vấn đáp thường gặp

**Q: Chaos Engineering khác gì với load testing?**
A: Load test verify system chịu được tải dự kiến (throughput, latency SLO). Chaos Engineering tìm reliability weakness từ component interaction — crash, partition, cascade failure — không liên quan đến tải. Load test chạy pre-launch; chaos chạy continuously in production.

**Q: Tại sao phải run chaos in production, không phải staging?**
A: §4 nguyên tắc 3: staging không reproduce được scale/traffic shape của prod. Một class of bug chỉ xuất hiện ở production scale — ví dụ: Roblox 2021 outage (§7.1) với Consul streaming contention chỉ xảy ra ở production load. Staging chaos = false confidence.

**Q: Blast radius là gì và tại sao quan trọng?**
A: Blast radius = phạm vi ảnh hưởng của experiment (số instance, % traffic, duration). Quan trọng vì chaos in production có thể gây thật user impact. Nguyên tắc: start nhỏ (1 instance, 1% traffic), có rollback automatic, monitor steady-state probe. Nếu probe_pass_rate < threshold → abort experiment ngay.

**Q: Topology-aware RCA hoạt động như thế nào?**
A: (1) Build directed graph từ topology.json. (2) Tính depth mỗi node (root=0, leaf=max). (3) Khi có incidents, sort alerting services by depth ASC. (4) Pick shallowest (most upstream) làm root_service. Tiebreak: earliest fire_ts. Điều này đảm bảo RCA không bị lừa bởi downstream services "to tiếng" hơn (§7.3).

**Q: External synthetic probe tốt hơn Prometheus metrics ở điểm nào?**
A: Probe đo *user-visible* outcome (HTTP response code + latency từ ngoài cluster). Prometheus đo *system-internal* metrics (có thể báo "200 OK" trong khi body sai, cache stale, partial degrade). Probe cũng catch được fault ở DNS, TLS, ingress, load balancer — những thứ Prometheus không scrape được vì scrape itself đi qua các layer đó.

**Q: False positive trong chaos là gì? Tại sao FA ≤ 1 là acceptance criteria?**
A: False positive = pipeline fire alert trong **baseline window** (khi không có fault inject). FA > 1 nghĩa là pipeline không đáng tin — nó "sợ" cả khi hệ thống healthy. FA ≤ 1 cho phép 1 transient false alarm trong 10 experiments (chấp nhận được vì noise). FA > 1 → phải tune thresholds, không tune để force pass.

**Q: Tại sao experiment 10 là "negative test"?**
A: Ground truth là `NOT checkout-svc`. Experiment validate rằng RCA **không pick checkout-svc** mặc dù checkout là service "to tiếng nhất" (nhiều errors nhất). RCA phải pick upstream root (payment-svc hoặc inventory-svc) vì checkout chỉ là symptom carrier của retry storm. Đây là test quan trọng nhất — validate §7.3 counter (topology-aware không bị lừa bởi alert count).

---

## 9. File Structure Overview

```
w3-d2-pack/
├── experiments.yaml              ← 10 experiments (5 fields each) — FILLED
├── chaos_results.json            ← Output from chaos_runner.py — 10 entries
├── probe.log                     ← External steady-state signal — xuyên suốt 10 experiments
├── baseline.json                 ← Prometheus snapshot 300s trước experiments
├── docker-compose.yml            ← 10-service stack + prometheus + alertmanager + toxiproxy
├── synthetic_probe.sh            ← External probe, poll /health every 5s
├── chaos_report.md               ← Full analysis report (§8.7)
├── SUBMIT.md                     ← Submission answers (§8.8)
├── DOC.md                        ← This file — comprehensive documentation
│
├── services/
│   └── service.py                ← Generic mock FastAPI service (all 10 services mount this)
│
├── pipeline/
│   ├── aiops_pipeline.py         ← AIOps FastAPI: /alerts /correlate /rca
│   └── chaos_runner.py           ← Runner implementing build_inject_cmd() + print_scoreboard()
│
├── configs/
│   ├── prometheus_chaos.yml      ← Scrape config for all 9 app services
│   ├── alert_rules_chaos.yml     ← HighLatency, HighErrorRate, InstanceDown, RetryStorm rules
│   ├── alertmanager_chaos.yml    ← Route config + inhibit rules
│   ├── toxiproxy.json            ← Toxiproxy upstream definitions
│   └── topology.json             ← Service dependency graph for RCA
│
└── scripts/
    ├── start_stack.sh            ← docker compose up + healthcheck wait
    ├── capture_baseline.py       ← Prometheus snapshot → baseline.json
    ├── query_pipeline.py         ← CLI to inspect /alerts /correlate /rca
    └── score_run.py              ← Scoreboard from chaos_results.json + probe.log
```

---

## 10. References

| Source | Topic |
|--------|-------|
| principlesofchaos.org (Rosenthal et al., 2017/2019) | 5 Chaos Engineering principles |
| Rosenthal & Jones, *Chaos Engineering*, O'Reilly 2020 | Experiment design template (§5) |
| about.roblox.com/newsroom/2022/01 | Roblox 2021 outage — §7.1 noise floor case study |
| Google SRE Workbook ch.5 | Black-box monitoring pattern (external probe) |
| github.com/Shopify/toxiproxy | Toxiproxy — deterministic network fault injection |
| github.com/alexei-led/pumba | Pumba — Docker chaos tool |
| chaos-mesh.org | Chaos Mesh — Kubernetes chaos tool |
| litmuschaos.io | LitmusChaos — Kubernetes chaos + CI/CD integration |
| aws.amazon.com/fis | AWS Fault Injection Simulator |
