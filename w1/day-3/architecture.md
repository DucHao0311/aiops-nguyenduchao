# Architecture: End-to-End AIOps Data Layer
## Use Case: Anomaly Detection on Payment Service

---

## Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        PAYMENT SERVICE MESH                                     │
│                                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  ┌──────────┐        │
│  │ payment- │   │ fraud-   │   │ auth-    │   │ wallet-  │  │ notif-   │        │
│  │ gateway  │   │ detector │   │ service  │   │ service  │  │ service  │        │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘  └────┬─────┘        │
│       │              │              │              │             │              │
│       └──────────────┴──────────────┴──────────────┴─────────────┘              │
│                                    │                                            │
│                          OTel SDK (auto-instrument)                             │
│                    (traces, metrics, logs — OTLP/gRPC)                          │
└────────────────────────────────────┬────────────────────────────────────────────┘
                                     │
                    ┌────────────────▼─────────────────┐
                    │        COLLECTION LAYER          │
                    │                                  │
                    │   OTel Collector (gateway mode)  │
                    │   ┌──────────────────────────┐   │
                    │   │  Receivers: OTLP, Filelog│   │
                    │   │  Processors: batch,      │   │
                    │   │    filter, transform,    │   │
                    │   │    k8sattributes         │   │
                    │   │  Exporters: Kafka        │   │
                    │   └──────────────────────────┘   │
                    └────────────────┬─────────────────┘
                                     │
                    ┌────────────────▼─────────────────┐
                    │        TRANSPORT LAYER           │
                    │                                  │
                    │         Apache Kafka             │
                    │   ┌──────────────────────────┐   │
                    │   │  Topics:                 │   │
                    │   │    payment.metrics       │   │
                    │   │    payment.logs          │   │
                    │   │    payment.traces        │   │
                    │   │  3-broker cluster        │   │
                    │   │  Retention: 7 days       │   │
                    │   └──────────────────────────┘   │
                    └────┬───────────┬─────────────────┘
                         │           │
           ┌─────────────▼───┐  ┌────▼───────────────────┐
           │  PROCESSING     │  │  PROCESSING            │
           │  (Real-time)    │  │  (Batch / Training)    │
           │                 │  │                        │
           │  Apache Flink   │  │  Apache Spark          │
           │  ┌───────────┐  │  │  ┌─────────────────┐   │
           │  │ jobs:     │  │  │  │ jobs:           │   │
           │  │ - feature │  │  │  │ - model train   │   │
           │  │   extract │  │  │  │ - backfill      │   │
           │  │ - anomaly │  │  │  │ - retraining    │   │
           │  │   score   │  │  │  └─────────────────┘   │
           │  │ - alert   │  │  │                        │
           │  └───────────┘  │  └────────────────────────┘
           └────────┬────────┘              │
                    │                       │
           ┌────────▼────────────────────────▼────────────┐
           │              STORAGE LAYER                   │
           │                                              │
           │  ┌───────────────┐  ┌───────────────────┐    │
           │  │VictoriaMetrics│  │ Grafana Loki      │    │
           │  │(TSDB metrics) │  │ (structured logs) │    │
           │  │90-day retain  │  │ 30-day retain     │    │
           │  └───────────────┘  └───────────────────┘    │
           │                                              │
           │  ┌───────────────┐  ┌───────────────────┐    │
           │  │ Jaeger / Tempo│  │ MinIO / GCS       │    │
           │  │ (traces)      │  │ (ML model store,  │    │
           │  │ 7-day retain  │  │  cold log archive)│    │
           │  └───────────────┘  └───────────────────┘    │
           │                                              │
           │  ┌───────────────────────────────────────┐   │
           │  │ Feature Store (Redis / Feast)         │   │
           │  │ (online: real-time scoring features)  │   │
           │  └───────────────────────────────────────┘   │
           └─────────────────────────┬────────────────────┘
                                     │
           ┌─────────────────────────▼────────────────────┐
           │            QUERY / ML LAYER                  │
           │                                              │
           │  ┌─────────────┐  ┌────────────────────────┐ │
           │  │  Grafana    │  │  ML Inference Service  │ │
           │  │  Dashboards │  │  (FastAPI + ONNX)      │ │
           │  │  Alerting   │  │  - Isolation Forest    │ │
           │  │  (PagerDuty)│  │  - LSTM Autoencoder    │ │
           │  └─────────────┘  └────────────────────────┘ │
           │                                              │
           │  ┌─────────────────────────────────────────┐ │
           │  │  MLflow (experiment tracking)           │ │
           │  │  + model registry + A/B deploy          │ │
           │  └─────────────────────────────────────────┘ │
           └──────────────────────────────────────────────┘
```

---

## Component Rationale

| Layer | Tool | Why |
|-------|------|-----|
| **Instrumentation** | OpenTelemetry SDK | Vendor-neutral; single SDK for traces + metrics + logs; CNCF standard |
| **Collection** | OTel Collector (gateway) | Batching, filtering, enrichment before Kafka; decouples services from backend |
| **Transport** | Apache Kafka | Durable, replayable; enables multiple consumers (Flink + Spark) from same stream; 7-day replay for model retraining |
| **Stream Processing** | Apache Flink | Stateful stream processing; sub-second latency; native watermark/window API for rolling features |
| **Batch Processing** | Apache Spark | Periodic model retraining on historical data; backfill features |
| **Metrics DB** | VictoriaMetrics | PromQL-compatible; 10× cheaper storage than InfluxDB; long-term retention built-in |
| **Log Storage** | Grafana Loki | LogQL; label-based index (not full-text) = low cost; integrates with Grafana |
| **Trace Storage** | Grafana Tempo | Free OSS; integrates with Grafana; object-storage backend (GCS/S3) |
| **ML Model Serving** | FastAPI + ONNX Runtime | Lightweight; ONNX = framework-agnostic; deploy Isolation Forest or LSTM |
| **Experiment Tracking** | MLflow | OSS; model registry + versioning; integrates with Spark |
| **Visualization** | Grafana | Unified dashboard for metrics + logs + traces + anomaly scores; alerting |
| **Feature Store** | Redis (online) + Feast | Real-time feature serving for inference; <1ms lookup |
| **Object Storage** | MinIO / GCS | Cheap cold storage for model artifacts + log archive |

---

## Data Flow — Payment Transaction Example

```
1. User pays → payment-gateway calls fraud-detector
2. OTel SDK auto-instruments both services
   → emit span (trace), counter (metric), structured log (JSON)
3. OTel Collector batches + enriches with k8s pod labels
   → exports to Kafka topics
4. Flink job consumes payment.metrics:
   → computes rolling_mean, rolling_std, z_score per 1-min tumbling window
   → scores via ML inference endpoint (FastAPI/ONNX)
   → if anomaly_score > threshold → write alert to payment.alerts topic
5. OTel Collector (alert consumer) reads payment.alerts
   → pushes to PagerDuty + writes to Loki
6. VictoriaMetrics stores raw + derived metrics
7. Grafana dashboard shows: TPS, error rate, anomaly score timeline
8. Nightly: Spark re-trains Isolation Forest on 30-day window
   → registers new model version in MLflow
   → Flink hot-reloads model (no restart)
```

---

## Deployment Topology (Personal Account / Low Cost)

For personal/startup deployment targeting a single GCP/AWS account:

```
Kubernetes (GKE Autopilot or EKS Fargate)
├── otel-collector        (1 pod, 0.5 CPU)
├── kafka                 (Confluent Cloud Serverless — pay per use)
├── flink-jobmanager      (1 pod, 1 CPU)
├── flink-taskmanager     (1–3 pods, autoscale)
├── victoria-metrics      (1 pod, 1 CPU, 50GB PVC)
├── loki                  (1 pod, 0.5 CPU, GCS backend)
├── tempo                 (1 pod, 0.5 CPU, GCS backend)
├── grafana               (1 pod, 0.25 CPU)
├── mlflow                (1 pod, 0.5 CPU, GCS artifact store)
└── ml-inference-api      (1 pod, 0.5 CPU, ONNX runtime)

Estimated personal monthly cost: ~$80–150/month (GKE + Confluent free tier)
```
