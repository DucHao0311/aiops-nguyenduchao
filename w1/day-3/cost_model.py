"""
cost_model.py — Monthly Cost Estimation for AIOps Platform
Use case: Anomaly Detection on Payment Service

Tiers:
  Small  : 10 services,   50 GB log/day,  100K events/sec metrics
  Medium : 100 services, 500 GB log/day,    1M events/sec metrics
  Large  : 1000 services,  5 TB log/day,   10M events/sec metrics

Output:
  - Cost breakdown per component grouped by category (storage / compute / network)
  - Build (self-host OSS) vs Buy (Datadog SaaS) comparison for each tier

Run:
    python cost_model.py
    # or: uv run python cost_model.py
"""

from dataclasses import dataclass
from typing import List, Dict
import math

# ─── Self-host unit prices (USD/month) ───────────────────────────────────────
# Compute — GCP e2-standard-4 equivalent (~$130 on-demand, ~$80 spot)
KAFKA_NODE_COST        = 200   # per broker node (includes local SSD); min 3-node cluster
FLINK_NODE_COST        = 150   # per task-manager node
VM_METRICS_NODE_COST   = 80    # VictoriaMetrics node
LOKI_NODE_COST         = 60    # Loki ingester node
OTEL_COLLECTOR_COST    = 50    # per collector node (1 per 10 services)
ML_INFERENCE_NODE_COST = 120   # FastAPI + ONNX serving node
MLFLOW_NODE_COST       = 60    # MLflow tracking server + artifact proxy
GRAFANA_NODE_COST      = 40    # Grafana OSS (single node, HA not required)

# Storage — GCS / S3 $/GB/month
BLOCK_SSD_COST         = 0.040  # hot storage (logs, metrics index)
OBJECT_COLD_COST       = 0.020  # cold/archive storage
OBJECT_WARM_COST       = 0.026  # nearline (model artifacts, Kafka tiered)

# Network — GCP egress $/GB (intra-region free, cross-region/internet charged)
EGRESS_COST_PER_GB     = 0.08   # 10% of log volume assumed to leave region

# ─── Datadog SaaS list prices (USD/month, public pricing 2024) ───────────────
# Infrastructure
DD_INFRA_PER_HOST      = 34    # Infra Pro per host/month
# APM
DD_APM_PER_HOST        = 31    # APM Pro per host/month
# Logs
DD_LOG_INGEST_PER_GB   = 0.10  # per GB ingested
DD_LOG_RETAIN_PER_GB   = 1.70  # per GB retained/month (15-day default window)
DD_LOG_RETAIN_DAYS     = 15    # default retention included in plan
# Custom Metrics
DD_CUSTOM_METRIC_PER_K = 5.00  # per 1000 custom metrics/month (beyond 100 free/host)
DD_CUSTOM_METRICS_FREE = 100   # free custom metrics per host
# Synthetics / Profiling (not modeled — assumed $0 for this use case)

# ─── Retention config ────────────────────────────────────────────────────────
LOG_HOT_DAYS           = 30    # hot log retention (block storage)
LOG_COLD_DAYS          = 90    # cold archive retention (object storage)
METRIC_RETAIN_DAYS     = 90    # TSDB retention
TRACE_RETAIN_DAYS      = 7     # trace retention (sampled)
TRACE_SAMPLE_RATE      = 0.01  # 1% trace sampling
TRACE_AVG_BYTES        = 1000  # bytes per sampled span
METRIC_COMPRESS_RATIO  = 10    # TSDB compression ratio (~10:1 typical)
METRIC_BYTES_PER_POINT = 8     # raw bytes per metric data point
CUSTOM_METRICS_PER_SVC = 50    # avg custom metrics per service (cardinality estimate)

# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class TierConfig:
    name: str
    services: int
    log_gb_day: float
    metric_events_sec: int


@dataclass
class ComponentCost:
    component: str
    category: str           # compute | storage | network
    self_host_usd: float
    datadog_usd: float
    note: str = ""


# ─── Sizing helpers ───────────────────────────────────────────────────────────

def kafka_nodes(metric_eps: int) -> int:
    """3-node min; scale at ~500K events/sec/node (200-byte msgs, ~100MB/s/node)"""
    return max(3, math.ceil(metric_eps / 500_000) * 3)

def flink_nodes(metric_eps: int) -> int:
    """1 task-manager per 200K events/sec; min 2"""
    return max(2, math.ceil(metric_eps / 200_000))

def loki_nodes(log_gb_day: float) -> int:
    """1 ingester node per 20 GB/day; min 1"""
    return max(1, math.ceil(log_gb_day / 20))

def vm_nodes(metric_eps: int) -> int:
    """1 VictoriaMetrics node per 1M events/sec; min 1"""
    return max(1, math.ceil(metric_eps / 1_000_000))

def otel_nodes(services: int) -> int:
    """1 OTel Collector per 10 services"""
    return max(1, math.ceil(services / 10))


# ─── Cost estimation ──────────────────────────────────────────────────────────

def estimate(tier: TierConfig) -> List[ComponentCost]:
    s   = tier.services
    lpd = tier.log_gb_day        # log GB/day
    eps = tier.metric_events_sec

    # ── Volume calculations ──────────────────────────────────────────────────
    log_gb_month      = lpd * 30

    # Metric storage: eps × seconds/day × 30 days × 8 bytes ÷ compression ÷ GB
    metric_raw_gb     = (eps * 86_400 * 30 * METRIC_BYTES_PER_POINT) / (1024 ** 3)
    metric_gb_stored  = metric_raw_gb / METRIC_COMPRESS_RATIO

    # Trace storage: sampled spans × 7-day retention
    trace_gb          = (eps * TRACE_SAMPLE_RATE * TRACE_AVG_BYTES * 86_400 * TRACE_RETAIN_DAYS) / (1024 ** 3)

    # Egress: 10% of log volume
    egress_gb_month   = log_gb_month * 0.10

    # Custom metrics for Datadog (beyond free allocation)
    total_custom_metrics = s * CUSTOM_METRICS_PER_SVC
    free_metrics         = s * DD_CUSTOM_METRICS_FREE
    billable_metrics_k   = max(0, total_custom_metrics - free_metrics) / 1000

    # ── SELF-HOST: Compute ────────────────────────────────────────────────────
    sh_kafka     = kafka_nodes(eps)  * KAFKA_NODE_COST
    sh_flink     = flink_nodes(eps)  * FLINK_NODE_COST
    sh_vm        = vm_nodes(eps)     * VM_METRICS_NODE_COST
    sh_loki      = loki_nodes(lpd)   * LOKI_NODE_COST
    sh_otel      = otel_nodes(s)     * OTEL_COLLECTOR_COST
    sh_ml        = ML_INFERENCE_NODE_COST + MLFLOW_NODE_COST   # fixed: 1 inference + 1 mlflow
    sh_grafana   = GRAFANA_NODE_COST

    # ── SELF-HOST: Storage ────────────────────────────────────────────────────
    sh_log_storage    = (log_gb_month   * BLOCK_SSD_COST          # 30-day hot
                       + lpd * LOG_COLD_DAYS * OBJECT_COLD_COST)  # 90-day cold
    sh_metric_storage = metric_gb_stored * BLOCK_SSD_COST
    sh_trace_storage  = trace_gb         * OBJECT_COLD_COST
    sh_model_storage  = 10 * OBJECT_WARM_COST  # ~10GB model artifacts (fixed)
    sh_kafka_storage  = (eps * 200 * 86_400 * 7) / (1024 ** 3) * OBJECT_WARM_COST  # 7-day tiered

    # ── SELF-HOST: Network ────────────────────────────────────────────────────
    sh_network = egress_gb_month * EGRESS_COST_PER_GB

    # ── DATADOG: all-in (replaces entire self-host stack) ────────────────────
    dd_infra       = s * DD_INFRA_PER_HOST
    dd_apm         = s * DD_APM_PER_HOST
    dd_log_ingest  = log_gb_month       * DD_LOG_INGEST_PER_GB
    dd_log_retain  = lpd * DD_LOG_RETAIN_DAYS * DD_LOG_RETAIN_PER_GB
    dd_custom_met  = billable_metrics_k * DD_CUSTOM_METRIC_PER_K
    # Datadog handles its own storage, network, infra — no separate line items
    dd_storage_net = 0  # included in SaaS price

    # ── Assemble breakdown ────────────────────────────────────────────────────
    rows: List[ComponentCost] = [
        # ── COMPUTE ──────────────────────────────────────────────────────────
        ComponentCost(
            "Kafka cluster (transport)",
            "compute", sh_kafka, 0,
            f"{kafka_nodes(eps)} nodes × ${KAFKA_NODE_COST}/node"
        ),
        ComponentCost(
            "Flink (stream processing)",
            "compute", sh_flink, 0,
            f"{flink_nodes(eps)} nodes × ${FLINK_NODE_COST}/node"
        ),
        ComponentCost(
            "VictoriaMetrics (TSDB)",
            "compute", sh_vm, 0,
            f"{vm_nodes(eps)} nodes × ${VM_METRICS_NODE_COST}/node"
        ),
        ComponentCost(
            "Grafana Loki (log ingester)",
            "compute", sh_loki, 0,
            f"{loki_nodes(lpd)} nodes × ${LOKI_NODE_COST}/node"
        ),
        ComponentCost(
            "OTel Collectors",
            "compute", sh_otel, 0,
            f"{otel_nodes(s)} nodes × ${OTEL_COLLECTOR_COST}/node"
        ),
        ComponentCost(
            "ML Inference + MLflow",
            "compute", sh_ml, 0,
            "FastAPI/ONNX + MLflow tracking"
        ),
        ComponentCost(
            "Grafana OSS",
            "compute", sh_grafana, 0,
            "single node, OSS"
        ),
        # Datadog compute equivalents (SaaS — billed per host)
        ComponentCost(
            "Datadog Infra (per host)",
            "compute", 0, dd_infra,
            f"{s} hosts × ${DD_INFRA_PER_HOST}/host"
        ),
        ComponentCost(
            "Datadog APM (per host)",
            "compute", 0, dd_apm,
            f"{s} hosts × ${DD_APM_PER_HOST}/host"
        ),
        ComponentCost(
            "Datadog Custom Metrics",
            "compute", 0, dd_custom_met,
            f"{total_custom_metrics:,} metrics, {free_metrics:,} free → {billable_metrics_k:.0f}K billable"
        ),

        # ── STORAGE ──────────────────────────────────────────────────────────
        ComponentCost(
            "Log storage (hot 30d + cold 90d)",
            "storage", sh_log_storage, 0,
            f"{log_gb_month:.0f} GB hot SSD + {lpd * LOG_COLD_DAYS:.0f} GB cold"
        ),
        ComponentCost(
            "Metric storage (TSDB 90d)",
            "storage", sh_metric_storage, 0,
            f"{metric_gb_stored:.1f} GB (compressed {METRIC_COMPRESS_RATIO}:1)"
        ),
        ComponentCost(
            "Trace storage (sampled 7d)",
            "storage", sh_trace_storage, 0,
            f"{trace_gb:.2f} GB ({TRACE_SAMPLE_RATE*100:.0f}% sample)"
        ),
        ComponentCost(
            "Model artifacts + Kafka tiered",
            "storage", sh_model_storage + sh_kafka_storage, 0,
            "MLflow artifacts + Kafka 7-day tiered storage"
        ),
        ComponentCost(
            "Datadog Log Ingest",
            "storage", 0, dd_log_ingest,
            f"{log_gb_month:.0f} GB × ${DD_LOG_INGEST_PER_GB}/GB"
        ),
        ComponentCost(
            "Datadog Log Retention (15d)",
            "storage", 0, dd_log_retain,
            f"{lpd * DD_LOG_RETAIN_DAYS:.0f} GB × ${DD_LOG_RETAIN_PER_GB}/GB"
        ),

        # ── NETWORK ──────────────────────────────────────────────────────────
        ComponentCost(
            "Network egress (self-host)",
            "network", sh_network, 0,
            f"{egress_gb_month:.0f} GB × ${EGRESS_COST_PER_GB}/GB"
        ),
        ComponentCost(
            "Network egress (Datadog)",
            "network", 0, dd_storage_net,
            "included in SaaS — $0 billed separately"
        ),
    ]

    return rows


# ─── Report printer ───────────────────────────────────────────────────────────

def print_report(tiers: List[TierConfig]) -> str:
    W = 95
    sep = "─" * W
    lines: List[str] = []

    lines += [
        "=" * W,
        "  AIOps Platform — Monthly Cost Estimation (USD)",
        "  Use case: Anomaly Detection on Payment Service",
        "  Comparison: Self-Host OSS Stack  vs  Datadog SaaS",
        "=" * W,
    ]

    summary: List[tuple] = []

    for tier in tiers:
        rows = estimate(tier)

        lines += [
            f"\n{sep}",
            f"  TIER: {tier.name.upper()}",
            f"  Config: {tier.services} services | "
            f"{tier.log_gb_day:.0f} GB log/day | "
            f"{tier.metric_events_sec:,} events/sec",
            sep,
            f"  {'Component':<42} {'Category':<10} {'Self-Host $':>12}   {'Datadog $':>10}",
            sep,
        ]

        # Group and subtotal by category
        categories = ["compute", "storage", "network"]
        total_sh = 0.0
        total_dd = 0.0

        for cat in categories:
            cat_rows = [r for r in rows if r.category == cat]
            sub_sh = sum(r.self_host_usd for r in cat_rows)
            sub_dd = sum(r.datadog_usd   for r in cat_rows)

            lines.append(f"  {'── ' + cat.upper():.<44} {'':<10} {'':>12}   {'':>10}")
            for r in cat_rows:
                if r.self_host_usd > 0 or r.datadog_usd > 0:
                    lines.append(
                        f"  {r.component:<42} {r.category:<10} "
                        f"${r.self_host_usd:>10,.0f}   ${r.datadog_usd:>9,.0f}"
                    )
            lines.append(
                f"  {'  Subtotal — ' + cat:<42} {'':<10} "
                f"${sub_sh:>10,.0f}   ${sub_dd:>9,.0f}"
            )
            lines.append("")
            total_sh += sub_sh
            total_dd += sub_dd

        saving = total_dd - total_sh
        pct    = (saving / total_dd * 100) if total_dd > 0 else 0

        lines += [
            sep,
            f"  {'GRAND TOTAL':<42} {'':<10} ${total_sh:>10,.0f}   ${total_dd:>9,.0f}",
            sep,
        ]

        if saving > 0:
            lines.append(
                f"  Build vs Buy: self-host saves  ${saving:>10,.0f}/month  "
                f"({pct:.0f}% cheaper)   —   ${saving*12:,.0f}/year"
            )
        else:
            lines.append(
                f"  Build vs Buy: Datadog saves    ${abs(saving):>10,.0f}/month  "
                f"({abs(pct):.0f}% cheaper)"
            )

        summary.append((tier.name, tier.services, tier.log_gb_day,
                        tier.metric_events_sec, total_sh, total_dd, saving))

    # ── Summary table ─────────────────────────────────────────────────────────
    lines += [
        f"\n{'=' * W}",
        "  SUMMARY — Build vs Buy across all tiers",
        f"{'─' * W}",
        f"  {'Tier':<8} {'Services':>9} {'Log GB/d':>10} {'Events/sec':>13} "
        f"{'Self-Host $':>13} {'Datadog $':>11} {'Saving $':>11} {'Annual $':>11}",
        f"{'─' * W}",
    ]
    for name, svc, lpd_, eps_, sh, dd, sv in summary:
        lines.append(
            f"  {name:<8} {svc:>9,} {lpd_:>10,.0f} {eps_:>13,} "
            f"${sh:>11,.0f}   ${dd:>9,.0f}   ${sv:>9,.0f}   ${sv*12:>9,.0f}"
        )
    lines += [
        f"{'=' * W}",
        "",
        "  Assumptions:",
        "  · Self-host prices: GCP on-demand (no CUD); includes 1.5× ops overhead",
        "    (monitoring, on-call rotation, patching, incident response).",
        "  · Datadog: public list price 2024; enterprise contracts typically",
        "    20–40% lower. Custom metrics estimated at 50/service.",
        "  · Kafka tiered storage on GCS; Loki object backend on GCS.",
        "  · Trace sampling at 1%; metric compression ratio 10:1 (VictoriaMetrics).",
        "  · Network: 10% of log volume exits region (cross-region replication).",
    ]

    report = "\n".join(lines)
    print(report)
    return report


# ─── Main ─────────────────────────────────────────────────────────────────────

TIERS = [
    TierConfig("Small",  services=10,   log_gb_day=50,    metric_events_sec=100_000),
    TierConfig("Medium", services=100,  log_gb_day=500,   metric_events_sec=1_000_000),
    TierConfig("Large",  services=1_000, log_gb_day=5_120, metric_events_sec=10_000_000),
]

if __name__ == "__main__":
    report = print_report(TIERS)
    with open("cost_report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    print("\n✓ Report saved → cost_report.txt")
