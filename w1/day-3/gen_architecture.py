"""
gen_architecture.py — Generate architecture.png using Python diagrams library
Use case: Anomaly Detection on Payment Service (E2E AIOps Data Layer)

Mirrors the full architecture described in architecture.md:
  Service (OTel SDK)
    → Collection  (OTel Collector)
    → Transport   (Kafka)
    → Processing  (Flink real-time  |  Spark batch)
    → Storage     (VictoriaMetrics, Loki, Tempo, MinIO/GCS, Redis/Feast)
    → Query/ML    (Grafana, FastAPI ONNX inference, MLflow)

Run:
    python gen_architecture.py
Output:
    architecture.png  (in current directory)
"""

import os
import sys
import shutil

# ── Ensure Graphviz bin is on PATH (Windows default install location) ─────────
GRAPHVIZ_BIN = r"C:\Program Files\Graphviz\bin"
if os.path.isdir(GRAPHVIZ_BIN) and GRAPHVIZ_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = GRAPHVIZ_BIN + os.pathsep + os.environ.get("PATH", "")

if shutil.which("dot") is None:
    print("ERROR: Graphviz 'dot' binary not found on PATH.")
    print("  Windows : winget install --id Graphviz.Graphviz")
    print("  macOS   : brew install graphviz")
    print("  Ubuntu  : sudo apt-get install graphviz")
    sys.exit(1)

print(f"✓ Graphviz found: {shutil.which('dot')}")

# ── Diagrams imports ──────────────────────────────────────────────────────────
from diagrams import Diagram, Cluster, Edge

# Messaging / Queue
from diagrams.onprem.queue      import Kafka

# Monitoring & Logging
from diagrams.onprem.monitoring import Grafana, Prometheus
from diagrams.onprem.logging    import Loki, FluentBit       # FluentBit = OTel Collector proxy

# Analytics / Processing
from diagrams.onprem.analytics  import Flink, Spark

# Compute (generic server node)
from diagrams.onprem.compute    import Server

# Database — on-prem (VictoriaMetrics proxy = InfluxDB icon, Tempo proxy = Cassandra)
from diagrams.onprem.database   import InfluxDB, Cassandra

# GCP Storage (MinIO / GCS object store)
from diagrams.gcp.storage       import GCS

# GCP ML (MLflow proxy)
from diagrams.gcp.ml            import AIPlatform as MLflow

# AWS (Redis/Feast proxy)
from diagrams.aws.database      import ElasticacheForRedis as Redis

# Python logo for FastAPI inference service
from diagrams.programming.language import Python

# ── Diagram ───────────────────────────────────────────────────────────────────

graph_attr = {
    "fontsize":  "13",
    "bgcolor":   "white",
    "pad":       "0.6",
    "splines":   "ortho",
    "nodesep":   "0.7",
    "ranksep":   "1.1",
}

node_attr = {
    "fontsize": "10",
}

OUTPUT_FILE = "architecture"  # diagrams appends .png automatically

with Diagram(
    "AIOps — Anomaly Detection on Payment Service",
    filename=OUTPUT_FILE,
    outformat="png",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
    node_attr=node_attr,
):

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 1 — Payment Service Mesh
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Payment Service Mesh\n(OTel SDK auto-instrument)"):
        svc_gw     = Server("payment-gateway")
        svc_fraud  = Server("fraud-detector")
        svc_auth   = Server("auth-service")
        svc_wallet = Server("wallet-service")
        svc_notif  = Server("notif-service")
        services   = [svc_gw, svc_fraud, svc_auth, svc_wallet, svc_notif]

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 2 — Collection
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Collection Layer"):
        otel = FluentBit("OTel Collector\n(gateway)\nOTLP / gRPC")

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 3 — Transport
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Transport Layer\n(Apache Kafka — 7-day retention)"):
        kafka_metrics = Kafka("payment.metrics")
        kafka_logs    = Kafka("payment.logs")
        kafka_traces  = Kafka("payment.traces")

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 4a — Stream Processing (real-time)
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Stream Processing\n(Flink — real-time <2s)"):
        flink_feat  = Flink("feature\nextraction")
        flink_score = Flink("anomaly\nscoring")
        flink_alert = Flink("alert\nrouting")

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 4b — Batch Processing (nightly)
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Batch Processing\n(Spark — nightly retraining)"):
        spark_train = Spark("model\ntraining")
        spark_fill  = Spark("backfill\nfeatures")

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 5 — Storage
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Storage Layer"):
        vm      = InfluxDB("VictoriaMetrics\n(TSDB metrics)\n90-day retain")
        loki    = Loki("Grafana Loki\n(logs)\n30-day retain")
        tempo   = Cassandra("Grafana Tempo\n(traces)\n7-day retain")
        minio   = GCS("MinIO / GCS\n(cold archive\n+ model store)")
        redis   = Redis("Redis + Feast\n(feature store\nonline <1ms)")

    # ────────────────────────────────────────────────────────────────────────
    # LAYER 6 — Query / ML
    # ────────────────────────────────────────────────────────────────────────
    with Cluster("Query / ML Layer"):
        grafana = Grafana("Grafana\n(dashboards\n+ alerting)")
        ml_api  = Python("FastAPI + ONNX\nIsolation Forest\nLSTM Autoencoder")
        mlflow  = MLflow("MLflow\n(experiment tracking\n+ model registry)")

    # ────────────────────────────────────────────────────────────────────────
    # EDGES — data flow
    # ────────────────────────────────────────────────────────────────────────

    # Services → OTel Collector
    for svc in services:
        svc >> Edge(color="#4a90d9", style="bold") >> otel

    # OTel Collector → Kafka topics
    otel >> Edge(label="metrics", color="#e67e22", style="bold") >> kafka_metrics
    otel >> Edge(label="logs",    color="#7f8c8d", style="dashed") >> kafka_logs
    otel >> Edge(label="traces",  color="#95a5a6", style="dashed") >> kafka_traces

    # Kafka metrics → Flink stream
    kafka_metrics >> Edge(label="consume\nstream", color="#27ae60", style="bold") >> flink_feat
    flink_feat    >> Edge(color="#27ae60", style="bold") >> flink_score
    flink_score   >> Edge(color="#27ae60", style="bold") >> flink_alert

    # Flink ↔ ML inference (scoring loop)
    flink_score >> Edge(label="score\nrequest", color="#e74c3c", style="bold") >> ml_api
    ml_api      >> Edge(label="anomaly\nscore",  color="#e74c3c", style="bold") >> flink_score

    # Kafka metrics + logs → Spark batch
    kafka_metrics >> Edge(label="batch\nwindow", color="#8e44ad", style="dashed") >> spark_train
    kafka_logs    >> Edge(color="#8e44ad", style="dashed") >> spark_fill

    # Flink → Storage
    flink_feat  >> Edge(label="features",       color="#16a085") >> redis
    flink_alert >> Edge(label="alert logs",     color="#e74c3c") >> loki
    flink_feat  >> Edge(label="metric points",  color="#27ae60") >> vm

    # Kafka logs → Loki directly
    kafka_logs >> Edge(label="log ingest", color="#7f8c8d", style="dashed") >> loki

    # Kafka traces → Tempo
    kafka_traces >> Edge(label="trace ingest", color="#95a5a6", style="dashed") >> tempo

    # Spark → Storage / MLflow
    spark_train >> Edge(label="artifacts", color="#8e44ad", style="dashed") >> minio
    spark_train >> Edge(label="register",  color="#8e44ad", style="dashed") >> mlflow

    # MLflow → ML API (hot-reload)
    mlflow >> Edge(label="hot-reload\nmodel", color="#c0392b", style="dashed") >> ml_api

    # Storage → Grafana (query paths)
    vm    >> Edge(label="PromQL",  color="#e67e22") >> grafana
    loki  >> Edge(label="LogQL",   color="#e67e22") >> grafana
    tempo >> Edge(label="TraceQL", color="#e67e22") >> grafana

    # Cold archive
    minio >> Edge(label="archive", color="#bdc3c7", style="dotted") >> loki

# ── Done ─────────────────────────────────────────────────────────────────────
import pathlib
png = pathlib.Path(f"{OUTPUT_FILE}.png")
if png.exists():
    print(f"\n✓ Diagram saved → {png.resolve()}")
    print(f"  File size: {png.stat().st_size / 1024:.1f} KB")
else:
    print("✗ PNG not found — check Graphviz installation")
