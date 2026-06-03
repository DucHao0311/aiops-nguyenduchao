# SUBMIT.md — AIOps Platform Engineering

**Use case**: Anomaly Detection on Payment Service
**Dataset**: NAB `machine_temperature_system_failure.csv` — 22,695 rows, 5-min granularity
**Stack**: OTel SDK → Kafka → Flink / Spark → VictoriaMetrics / Loki / Tempo → Grafana + ML Inference

---

## Phase 1 — Mock Streaming Pipeline (`pipeline.py`)

### Mô tả

`pipeline.py` mô phỏng một **streaming pipeline** theo mô hình producer/consumer, dùng `queue.Queue` làm fake Kafka topic. Kiến trúc phản ánh cách Flink/Spark Streaming hoạt động ở scale lớn — chỉ khác ở transport layer (queue thay vì Kafka broker thật).

**Data source**: `realKnownCause/machine_temperature_system_failure.csv` (NAB dataset, 22,695 rows, 5-min granularity từ 2013-12 đến 2014-02).

**Luồng xử lý**:

```
CSV file (22,695 rows)
    │
    ▼
Producer Thread ──────────────────────────────► events.jsonl
    │  (đọc từng row, emit event dict,           (fake Kafka topic — 1 JSON/line)
    │   put vào queue.Queue)
    │
    ▼ queue.Queue(maxsize=1000)   ← fake Kafka in-process
    │
    ▼
Consumer Thread
    │  (loop queue.get() per event)
    │  (deque rolling buffer — stream-style, không load full data)
    │  (tính features per event)
    │
    ▼
features.parquet  (22,695 rows × 8 columns)
```

**Threading**: producer và consumer chạy trên 2 thread riêng biệt — simulate Kafka producer/consumer decoupled model. Sentinel value `None` báo hiệu kết thúc stream.

**Feature engineering (stream-style, online per event)**:

| Feature | Window | Cách tính |
|---------|--------|-----------|
| `rolling_mean_12` | 12 × 5min = 60 min | mean của `deque(maxlen=12)` |
| `rolling_std_12` | 60 min | std của `deque(maxlen=12)` |
| `rolling_mean_60` | 60 × 5min = 300 min | mean của `deque(maxlen=60)` |
| `rolling_std_60` | 300 min | std của `deque(maxlen=60)` |
| `rate_of_change` | t vs t-1 | `(value − prev) / (|prev| + ε)` |
| `z_score` | 60 min | `(value − mean₁₂) / (std₁₂ + ε)` — anomaly signal |

Các feature được tính **online per event** dùng `collections.deque(maxlen=N)` — không load toàn bộ data vào memory, đúng với stream processing semantics.

**Output files**:
- `features.parquet` — 22,695 rows × 8 columns, 1.7 MB
- `events.jsonl` — 22,695 raw events (1 JSON object/line, simulate Kafka topic log)

**Chạy**:
```bash
python pipeline.py
# hoặc: uv run python pipeline.py
```

### Kết quả

```
=======================================================
  AIOps Mock Streaming Pipeline
  Use case: Payment Service Anomaly Detection
=======================================================
[Producer] Starting — reading realKnownCause\machine_temperature_system_failure.csv
[Consumer] Starting — waiting for events…
[Consumer] Processed 5000 events…
[Consumer] Processed 10000 events…
[Consumer] Processed 15000 events…
[Consumer] Processed 20000 events…
[Producer] Done — emitted 22695 events
[Consumer] Done — 22695 events → features.parquet

               timestamp      value  rolling_mean_12   z_score  rate_of_change
22690 2014-02-19 15:05:00  98.185415        97.050779  1.394956        0.008469
22691 2014-02-19 15:10:00  97.804168        97.238123  0.810616       -0.003883
22692 2014-02-19 15:15:00  97.135468        97.324114 -0.308541       -0.006837
22693 2014-02-19 15:20:00  98.056852        97.498760  1.155563        0.009486
22694 2014-02-19 15:25:00  96.903861        97.427342 -1.042970       -0.011758

Schema:
timestamp          datetime64[ns]
value                     float64
rolling_mean_12           float64
rolling_std_12            float64
rolling_mean_60           float64
rolling_std_60            float64
rate_of_change            float64
z_score                   float64

Output size: 1736.4 KB
✓ Pipeline finished in 4.09s
```

### Screenshot

<Screenshot — terminal output của pipeline.py: producer/consumer threads, processed events log, tail 5 rows của features dataframe với các cột timestamp/value/rolling_mean_12/z_score/rate_of_change, schema, output size và thời gian chạy>

---

## Phase 1 — E2E Architecture Diagram (`architecture.md` / `architecture.png`)

### Mô tả

Kiến trúc E2E data layer cho use case **Anomaly Detection on Payment Service**, bao gồm 6 layer từ instrumentation đến query/ML. Tool được chọn cụ thể cho từng component với lý do rõ ràng.

**Data flow — Payment transaction example**:
1. User pays → `payment-gateway` gọi `fraud-detector` → OTel SDK auto-instrument emit span + metric + log
2. OTel Collector batch + enrich với k8s metadata → export ra Kafka topics
3. Flink consume `payment.metrics` → tính rolling features → score anomaly qua FastAPI/ONNX → nếu `z_score > 3σ` → write alert
4. VictoriaMetrics lưu raw + derived metrics; Loki lưu logs; Tempo lưu traces
5. Grafana hiển thị TPS, error rate, anomaly score timeline + PagerDuty alerting
6. Nightly: Spark retrain Isolation Forest trên 30-day window → register model mới vào MLflow → Flink hot-reload

**Component rationale**:

| Layer | Tool | Lý do chọn |
|-------|------|-----------|
| Instrumentation | OpenTelemetry SDK | Vendor-neutral, CNCF standard, 1 SDK cho trace + metric + log |
| Collection | OTel Collector (gateway mode) | Batch, filter, enrich — decouples service từ backend |
| Transport | Apache Kafka | Durable, replayable 7 ngày, multi-consumer fan-out |
| Stream Processing | Apache Flink | Stateful window API, sub-second latency, rolling features |
| Batch / Training | Apache Spark | Nightly model retraining trên 30-day history |
| Metrics DB | VictoriaMetrics | PromQL-compatible, 10× cheaper storage hơn InfluxDB |
| Log Storage | Grafana Loki | Label-based index (không full-text) = chi phí thấp |
| Trace Storage | Grafana Tempo | OSS, GCS/S3 backend, tích hợp với Grafana |
| ML Serving | FastAPI + ONNX Runtime | Framework-agnostic, <5ms inference |
| Experiment Tracking | MLflow | Model registry + versioning + A/B deploy |
| Feature Store | Redis + Feast | Online feature serving <1ms |
| Visualization | Grafana | Unified: metric + log + trace + anomaly score |

**Gen diagram**:
```bash
python gen_architecture.py   # output: architecture.png
```

### Kết quả

File `architecture.png` generated bởi Python `diagrams` library (Graphviz backend), 330 KB.

### Screenshot

<Screenshot — architecture.png: sơ đồ E2E AIOps data layer với 6 layer (Service Mesh → OTel Collector → Kafka topics → Flink stream / Spark batch → Storage cluster → Grafana / ML API / MLflow), các mũi tên màu phân biệt loại data flow (metric/log/trace/alert)>

---

## Phase 2 — Cost Estimation (`cost_model.py`)

### Mô tả

`cost_model.py` ước tính chi phí hàng tháng cho 3 tier scale, breakdown theo 3 category **(compute / storage / network)**, so sánh **self-host OSS** vs **Datadog SaaS**.

**3 tiers**:
| Tier | Services | Log/day | Metric events/sec |
|------|---------|---------|------------------|
| Small | 10 | 50 GB | 100,000 |
| Medium | 100 | 500 GB | 1,000,000 |
| Large | 1,000 | 5,120 GB (5 TB) | 10,000,000 |

**Methodology**:
- **Compute self-host**: sizing theo throughput thực tế (Kafka: 1 node/500K eps, Flink: 1 node/200K eps, Loki: 1 node/20 GB/day), nhân đơn giá GCP on-demand, bao gồm 1.5× ops overhead
- **Storage self-host**: log hot 30-day (SSD $0.04/GB) + cold 90-day (object $0.02/GB) + TSDB compressed 10:1 + trace 1% sampling 7-day + Kafka tiered storage
- **Network self-host**: 10% log volume egress × $0.08/GB (GCP)
- **Datadog SaaS**: $34/host/month infra + $31/host APM + $0.10/GB log ingest + $1.70/GB log retain (15-day) + custom metrics ($5/1K metrics/month)

**Chạy**:
```bash
python cost_model.py   # output: cost_report.txt
```

### Kết quả

#### Tier SMALL — 10 services | 50 GB log/day | 100,000 events/sec

| Category | Component | Self-Host $/month | Datadog $/month |
|----------|-----------|------------------:|----------------:|
| **compute** | Kafka cluster (transport) | $600 | — |
| compute | Flink (stream processing) | $300 | — |
| compute | VictoriaMetrics (TSDB) | $80 | — |
| compute | Grafana Loki (log ingester) | $180 | — |
| compute | OTel Collectors | $50 | — |
| compute | ML Inference + MLflow | $180 | — |
| compute | Grafana OSS | $40 | — |
| compute | Datadog Infra (per host) | — | $340 |
| compute | Datadog APM (per host) | — | $310 |
| | **Subtotal compute** | **$1,430** | **$650** |
| **storage** | Log storage (hot 30d + cold 90d) | $150 | — |
| storage | Metric storage (TSDB 90d) | $8 | — |
| storage | Trace storage (sampled 7d) | $11 | — |
| storage | Model artifacts + Kafka tiered | $293 | — |
| storage | Datadog Log Ingest | — | $150 |
| storage | Datadog Log Retention (15d) | — | $1,275 |
| | **Subtotal storage** | **$462** | **$1,425** |
| **network** | Network egress | $12 | $0 (included) |
| | **Subtotal network** | **$12** | **$0** |
| | **GRAND TOTAL** | **$1,904** | **$2,075** |
| | **Build vs Buy** | **Self-host saves $171/month (8% cheaper) — $2,050/year** | |

---

#### Tier MEDIUM — 100 services | 500 GB log/day | 1,000,000 events/sec

| Category | Component | Self-Host $/month | Datadog $/month |
|----------|-----------|------------------:|----------------:|
| **compute** | Kafka cluster | $1,200 | — |
| compute | Flink | $750 | — |
| compute | VictoriaMetrics | $80 | — |
| compute | Grafana Loki | $1,500 | — |
| compute | OTel Collectors | $500 | — |
| compute | ML Inference + MLflow | $180 | — |
| compute | Grafana OSS | $40 | — |
| compute | Datadog Infra | — | $3,400 |
| compute | Datadog APM | — | $3,100 |
| | **Subtotal compute** | **$4,250** | **$6,500** |
| **storage** | Log storage (hot+cold) | $1,500 | — |
| storage | Metric storage | $77 | — |
| storage | Trace storage | $113 | — |
| storage | Model artifacts + Kafka tiered | $2,929 | — |
| storage | Datadog Log Ingest | — | $1,500 |
| storage | Datadog Log Retention (15d) | — | $12,750 |
| | **Subtotal storage** | **$4,619** | **$14,250** |
| **network** | Network egress | $120 | $0 (included) |
| | **Subtotal network** | **$120** | **$0** |
| | **GRAND TOTAL** | **$8,989** | **$20,750** |
| | **Build vs Buy** | **Self-host saves $11,761/month (57% cheaper) — $141,130/year** | |

---

#### Tier LARGE — 1,000 services | 5,120 GB log/day | 10,000,000 events/sec

| Category | Component | Self-Host $/month | Datadog $/month |
|----------|-----------|------------------:|----------------:|
| **compute** | Kafka cluster | $12,000 | — |
| compute | Flink | $7,500 | — |
| compute | VictoriaMetrics | $800 | — |
| compute | Grafana Loki | $15,360 | — |
| compute | OTel Collectors | $5,000 | — |
| compute | ML Inference + MLflow | $180 | — |
| compute | Grafana OSS | $40 | — |
| compute | Datadog Infra | — | $34,000 |
| compute | Datadog APM | — | $31,000 |
| | **Subtotal compute** | **$40,880** | **$65,000** |
| **storage** | Log storage (hot+cold) | $15,360 | — |
| storage | Metric storage | $772 | — |
| storage | Trace storage | $1,127 | — |
| storage | Model artifacts + Kafka tiered | $29,290 | — |
| storage | Datadog Log Ingest | — | $15,360 |
| storage | Datadog Log Retention (15d) | — | $130,560 |
| | **Subtotal storage** | **$46,549** | **$145,920** |
| **network** | Network egress | $1,229 | $0 (included) |
| | **Subtotal network** | **$1,229** | **$0** |
| | **GRAND TOTAL** | **$88,658** | **$210,920** |
| | **Build vs Buy** | **Self-host saves $122,262/month (58% cheaper) — $1,467,147/year** | |

---

#### Summary — Build vs Buy

| Tier | Services | Self-Host/month | Datadog/month | Saving/month | Annual Saving |
|------|--------:|---------------:|-------------:|-------------:|-------------:|
| Small | 10 | $1,904 | $2,075 | **$171** | $2,050 |
| Medium | 100 | $8,989 | $20,750 | **$11,761** | $141,130 |
| Large | 1,000 | $88,658 | $210,920 | **$122,262** | $1,467,147 |

> Self-host: GCP on-demand + 1.5× ops overhead. Datadog: public list price 2024, enterprise discounts 20–40% không tính vào.
> Tại Small tier, self-host chỉ rẻ hơn 8% — **lợi thế không đáng kể** khi tính thêm ops cost thực tế.
> Tại Medium/Large, self-host tiết kiệm 57–58% — **ROI rõ ràng** khi có SRE team đủ lớn.

### Screenshot

<Screenshot — terminal output của cost_model.py: 3 tier breakdown có subtotal theo compute/storage/network, build vs buy saving cho từng tier, summary table cuối>

---

## Phase 3 — Architecture Decision Record (`ADR-001.md`)

### Mô tả

ADR-001 ghi lại quyết định kiến trúc lớn nhất trong pipeline: **dùng Kafka làm central transport** thay vì direct push từ OTel Collector thẳng vào các backend.

Format: Michael Nygard (Status → Context → Decision → Consequences → Alternatives Considered).

### Nội dung ADR

**Tiêu đề**: ADR-001 — Use Kafka as Central Transport for Payment Service Observability Pipeline

**Status**: Accepted (2026-06-03)

**Context**: Direct push architecture ban đầu gây 3 vấn đề nghiêm trọng ở peak load:
- ~8% event drop rate tại 300K events/sec (VictoriaMetrics write backpressure)
- Loki ingest lag 45s → real-time alerting không tin cậy
- Không có replay: nếu ML pipeline crash, data in-flight mất
- Fan-out coupling: thêm consumer mới = phải sửa toàn bộ Collector config

**Options evaluated**:

| Option | Throughput | Replay | Decoupling | Cost/month |
|--------|-----------|--------|------------|------------|
| Direct push (current) | 200K/sec trước khi drop | ✗ | ✗ tight | $0 |
| Direct push + rate limit | 200K/sec capped | ✗ | ✗ tight | $0 |
| Vector aggregator | 400K/sec | ✗ in-memory only | partial | ~$80 |
| **Kafka (chosen)** | **1M+/sec** | **✓ 7-day** | **✓ full** | **~$400–600** |
| Confluent Cloud Serverless | 1M+/sec | ✓ 7-day | ✓ full | ~$150–400 |

**Decision**: Introduce Kafka với 3 topics (`payment.metrics`, `payment.logs`, `payment.traces`), replication factor 3, 12 partitions/topic, 7-day retention.

**Quantified consequences**:

| Metric | Before | After (Kafka) | Delta |
|--------|--------|--------------|-------|
| Event drop rate | ~8% tại 300K/sec | ~0% | −8% |
| Replay | ✗ | ✓ 7 ngày | — |
| Consumer fan-out | ✗ tight | ✓ decoupled | — |
| Max throughput | 200K/sec | 1M+/sec | +5× |
| E2E telemetry latency | baseline | +8–12ms | acceptable (SLO = 30s) |
| Added infra cost | $0 | +$400–600/month | +$400–600 |
| Added ops overhead | 0.1 FTE | +0.25 FTE | +$1,200/month |
| Risk value recovered | — | ~$160K/month | 8% of $2M/day txn |

**Alternatives rejected**:
- Scale direct push horizontally: $50K+/month, không giải quyết replay/decoupling
- Rate limiting + sampling: systematic data loss — không chấp nhận cho payment data
- Vector aggregator: no replay, no multi-consumer fan-out
- Confluent Cloud Serverless: **được recommend cho startup <50 services** — zero ops overhead khi SRE team nhỏ

Chi tiết đầy đủ: [`ADR-001.md`](./ADR-001.md)

### Screenshot

<Screenshot — ADR-001.md mở trong editor: hiển thị phần Status, Context (bảng options comparison), Decision, Consequences (bảng quantified trade-offs), Alternatives Considered>

---

## Phase 4 — Reflection

### Build vs Buy cho Startup 50 Services vừa raise Series A?

**Kết luận: Hybrid có lộ trình rõ ràng — không phải "build" hay "buy" thuần túy.**

---

#### Tại sao không có câu trả lời tuyệt đối?

Quyết định build vs buy phụ thuộc vào ít nhất 5 tiêu chí độc lập nhau:

| Tiêu chí | Nghiêng về Buy | Nghiêng về Build |
|----------|---------------|-----------------|
| **Team size** | < 2 SRE dedicated | ≥ 3 SRE dedicated |
| **Log volume** | < 200 GB/day | > 300 GB/day |
| **Số services** | < 80 | > 100 |
| **Datadog bill** | < $5K/month | > $10K/month |
| **Compliance** | Không có đặc biệt | GDPR, HIPAA, PCI-DSS data residency |
| **ML requirements** | Generic alerting đủ | Domain-specific model cần thiết |
| **Stage** | Pre-PMF, cần move fast | Post-PMF, optimize cost |

Với startup **50 services + Series A vừa raise**: hầu hết tiêu chí đang ở vùng "Buy" hoặc trung gian.

---

#### Giai đoạn 1 (tháng 0–6): Buy

Dùng **Datadog** (hoặc Grafana Cloud nếu budget nhạy cảm) cho toàn bộ observability cơ bản.

Lý do:

- **Chi phí cơ hội**: 2 SRE mất 3 tháng setup Kafka + Flink + VictoriaMetrics = $30–50K salary cost + 3 tháng delayed product features. Trong khi Datadog Small tier = $2,075/month — rẻ hơn 1 junior SRE.
- **Time-to-value**: Datadog agent cài trong 30 phút, dashboard + alert hoạt động ngay. Self-host cần 2–4 tuần để ổn định production.
- **Series A metrics**: investor muốn thấy growth metrics, không muốn thấy team mất 3 tháng vào infra plumbing.

**Instrument bằng OTel SDK ngay từ ngày 1** dù dùng Datadog backend — đảm bảo vendor-neutral, migration về sau không cần sửa service code.

---

#### Giai đoạn 2 (tháng 6–18): Build ML/Anomaly layer song song

Triển khai **custom anomaly detection pipeline song song với Datadog**, không thay thế.

Lý do:

- Datadog Watchdog là black-box — không fine-tune được cho payment-specific patterns. Flash sale spike và thật sự anomaly trông giống nhau với generic threshold.
- False positive rate generic tool: 10–20%. Custom Isolation Forest/LSTM trained trên domain data: 2–5%. Mỗi false positive = 1 on-call page = $500–2,000 engineer time.
- Chi phí ML stack tự build: ~$500–800/month (Flink job + MLflow + FastAPI ONNX). ROI đạt được nếu giảm 2 false positive incidents/month.

**Tiêu chí để bắt đầu**:
- Có ≥ 1 ML engineer trong team
- Có ≥ 6 tháng production traffic data đã labeled
- False positive rate > 10% từ SaaS tool (alert fatigue rõ ràng)

---

#### Giai đoạn 3 (tháng 18–36): Migrate infra về self-host

Trigger cụ thể để migrate (không migrate sớm hơn):

| Trigger | Ngưỡng | Component ưu tiên migrate |
|---------|--------|--------------------------|
| Datadog bill | > $10K/month | Loki (log storage — item đắt nhất) |
| Log volume | > 300 GB/day | Self-host Loki + Kafka |
| Số services | > 80 | Evaluate VictoriaMetrics + Loki |
| SRE team | ≥ 3 dedicated | Đủ bandwidth vận hành |
| Compliance | Data residency (GDPR/HIPAA/PCI) | Bắt buộc — bất kể cost |
| Monthly saving | > $5K projected | ROI payback < 6 tháng → migrate |

Tại Medium tier (100 services): self-host tiết kiệm $11,761/month = $141,130/year — đủ hire 1 SRE senior ($120K/year) và còn dư $21K.

---

#### 6 việc tôi làm tuần đầu tiên nếu được hire:

1. **Audit observability hiện tại** — có gì rồi? CloudWatch? Prometheus tự dựng? Datadog trial? Đừng assume.
2. **Pull số liệu thực tế 30 ngày** — log GB/day, metric events/sec, số active services. Không ước tính.
3. **Map top-3 pain points theo business impact** — MTTR cao? Alert fatigue? Blind spot? Ưu tiên theo revenue risk, không theo technical elegance.
4. **Negotiate Datadog contract** nếu dùng SaaS — commit 1-year = 20–30% discount. Không pay month-to-month khi đã quyết định dùng.
5. **Viết ADR ngay** — define trigger rõ ràng khi nào sẽ migrate về self-host. Gắn với số thực tế (bill threshold, team size, volume). Tránh "khi nào thấy đắt thì tính" — quá trễ.
6. **Cài OTel SDK (vendor-neutral) cho tất cả services từ ngày 1** — dù backend là Datadog hay Grafana Cloud, instrument bằng OTel đảm bảo migration sau không cần sửa service code.

---

> **Bottom line**: Với startup 50-service Series A — **Buy Datadog ngay** ($2K/month) để move fast, instrument bằng OTel giữ vendor-neutral. **Build custom ML anomaly layer** trong 6 tháng (Datadog Watchdog không đủ cho payment domain). **Plan migrate** về self-host khi Datadog bill vượt $10K/month hoặc team có ≥ 3 SRE — không trước đó. Ở Small tier self-host chỉ tiết kiệm 8% ($171/month) — không đủ bù ops overhead của việc vận hành Kafka cluster khi chưa sẵn sàng.
