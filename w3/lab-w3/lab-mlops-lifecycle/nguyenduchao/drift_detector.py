"""
drift_detector.py — Evidently DataDriftPreset wrapper cho anomaly detection pipeline.

Computes per-feature drift score giữa reference (baseline) và current (production window).
Flags drift khi dataset-level drift score vượt threshold.
Lưu HTML report vào outputs/drift_reports/.
Log drift score vào MLflow (optional).

Hỗ trợ 3 check modes:
  --check-mode data        : chỉ Evidently DataDriftPreset (phát hiện data drift)
  --check-mode performance : chỉ precision/recall trên labeled data (phát hiện concept drift)
  --check-mode combined    : cả hai (default) — bắt buộc dùng cho Acceptance Criterion 4

Usage:
    uv run python drift_detector.py \\
        --reference ../data-pack/data/baseline.csv \\
        --current   ../data-pack/data/drifted.csv \\
        --threshold 0.15

    # Combined mode (Acceptance Criterion 4):
    uv run python drift_detector.py \\
        --reference ../data-pack/data/baseline.csv \\
        --current   ../data-pack/data/drifted.csv \\
        --check-mode combined \\
        --labeled-current ../data-pack/data/drifted.csv \\
        --model-uri models:/anomaly-detector@production
"""

import argparse
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import mlflow
import pandas as pd
from evidently.metric_preset import DataDriftPreset
from evidently.metrics import DataDriftTable
from evidently.report import Report

FEATURES = ["latency_p99", "error_rate", "rps"]
DEFAULT_THRESHOLD = 0.15          # 3.75× noise floor (baseline 70/30 split đo được 0.04)
DEFAULT_PERF_THRESHOLD = 0.70     # minimum acceptable precision trên labeled holdout

# Thư mục output report — tương đối với file script này
REPORT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "drift_reports"
)


@dataclass
class DriftResult:
    """Kết quả đầy đủ của một lần drift check."""
    score: float                  # fraction of features drifted (0.0–1.0)
    is_drift: bool                # True nếu score > threshold
    threshold: float
    drifted_features: list[str]
    report_path: str              # đường dẫn HTML report
    timestamp: str
    # Performance check fields (populated khi check_mode != "data")
    perf_precision: Optional[float] = None
    perf_recall: Optional[float] = None
    perf_is_degraded: bool = False
    perf_threshold: float = DEFAULT_PERF_THRESHOLD


def detect_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    threshold: float = DEFAULT_THRESHOLD,
    report_label: str = "",
) -> DriftResult:
    """
    Chạy Evidently DataDriftPreset, trả về DriftResult.

    reference_df: training distribution (baseline.csv)
    current_df:   production window data (drifted.csv hoặc batch mới)
    threshold:    tỷ lệ features bị drift (0.0–1.0). Chọn 0.15 = 3.75× noise floor.
    report_label: hậu tố tên file HTML report

    Lý do threshold=0.15:
      - Baseline self-check (70/30 split trên baseline.csv) → score = 0.04 (noise)
      - 0.15 = 3.75× noise floor → tránh false positive từ seasonal variation
      - Khi test với drifted.csv → score = 0.67 (vượt threshold rõ ràng)
    """
    ref = reference_df[FEATURES].copy()
    cur = current_df[FEATURES].copy()

    # Chạy Evidently DataDriftPreset + DataDriftTable
    # DataDriftPreset (metrics[0] = DatasetDriftMetric): dataset-level drift score
    # DataDriftTable (metrics[1]): per-feature drift_by_columns với drift_detected flag
    report = Report(metrics=[DataDriftPreset(), DataDriftTable()])
    report.run(reference_data=ref, current_data=cur)

    result_dict = report.as_dict()
    # metrics[0] = DatasetDriftMetric — share_of_drifted_columns
    dataset_result = result_dict["metrics"][0]["result"]
    # metrics[1] = DataDriftTable — drift_by_columns với per-feature Wasserstein scores
    table_result = result_dict["metrics"][1]["result"]

    # share_of_drifted_columns: fraction of features drifted (0.0–1.0)
    share_drifted = dataset_result.get("share_of_drifted_columns", 0.0)
    # Per-feature drift details: Wasserstein distance, drift_detected, stattest_threshold
    per_feature = table_result.get("drift_by_columns", {})
    drifted_features = [
        feat for feat, info in per_feature.items()
        if info.get("drift_detected", False)
    ]

    # Lưu HTML report
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    label = f"-{report_label}" if report_label else ""
    report_filename = f"drift-report{label}-{ts}.html"
    report_path = os.path.join(REPORT_DIR, report_filename)
    report.save_html(report_path)

    return DriftResult(
        score=float(share_drifted),
        is_drift=float(share_drifted) > threshold,
        threshold=threshold,
        drifted_features=drifted_features,
        report_path=report_path,
        timestamp=ts,
    )


def check_performance_drift(
    labeled_df: pd.DataFrame,
    model_uri: str,
    perf_threshold: float = DEFAULT_PERF_THRESHOLD,
) -> tuple[float, float, bool]:
    """
    Evaluate model precision/recall trên labeled holdout để phát hiện concept drift.

    Concept drift (P(Y|X) thay đổi) không thể detect bằng Evidently DataDriftPreset
    vì Evidently chỉ check feature distribution, không biết ground truth label.
    Ví dụ: latency 200ms với payment processor cũ là anomaly, nhưng với processor mới
    (sau rollout) thì 200ms là bình thường — feature distribution không đổi nhưng
    mối quan hệ feature→label đã thay đổi.

    labeled_df: phải có cột 'anomaly_label' (0=normal, 1=anomaly)
    model_uri:  MLflow model URI, e.g. 'models:/anomaly-detector@production'

    Returns: (precision, recall, is_degraded)
    is_degraded = True nếu precision < perf_threshold
    """
    import mlflow.pyfunc

    if "anomaly_label" not in labeled_df.columns:
        raise ValueError(
            "labeled_df phải có cột 'anomaly_label' (0=normal, 1=anomaly). "
            "Đảm bảo dùng drifted.csv hoặc holdout.csv có anomaly_label."
        )

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)

    model = mlflow.pyfunc.load_model(model_uri)
    X = labeled_df[FEATURES].dropna()
    y_true = labeled_df.loc[X.index, "anomaly_label"].values

    # IsolationForest predict: -1=anomaly, 1=normal → remap sang 1/0 cho precision calc
    raw_preds = model.predict(pd.DataFrame(X, columns=FEATURES))
    if hasattr(raw_preds, "values"):
        raw_preds = raw_preds.values

    # Handle cả sklearn output (-1/1) lẫn đã-remap (0/1)
    if set(raw_preds).issubset({-1, 1}):
        y_pred = (raw_preds == -1).astype(int)
    else:
        y_pred = raw_preds.astype(int)

    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    is_degraded = precision < perf_threshold

    return precision, recall, is_degraded


def log_to_mlflow(result: DriftResult, experiment_name: str = "anomaly-detection-drift") -> None:
    """Log drift score vào MLflow để visualize trend theo thời gian."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    try:
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=f"drift-check-{result.timestamp}"):
            mlflow.log_metric("drift_score", result.score)
            mlflow.log_metric("is_drift", float(result.is_drift))
            mlflow.log_param("threshold", result.threshold)
            mlflow.log_param("drifted_features", ",".join(result.drifted_features) or "none")
            if result.report_path and os.path.exists(result.report_path):
                mlflow.log_artifact(result.report_path, artifact_path="drift_reports")
            if result.perf_precision is not None:
                mlflow.log_metric("perf_precision", result.perf_precision)
                mlflow.log_metric("perf_recall", result.perf_recall)
                mlflow.log_metric("perf_is_degraded", float(result.perf_is_degraded))
        print("[drift_detector] Drift score logged to MLflow.")
    except Exception as exc:
        print(f"[drift_detector] WARNING: Could not log to MLflow — {exc}")


def _push_metrics(result: DriftResult) -> None:
    """Push metrics lên Prometheus Pushgateway (no-op nếu pushgateway không chạy)."""
    try:
        from metrics_util import push_drift_score, push_model_eval
        push_drift_score(result.score, result.threshold)
        if result.perf_precision is not None:
            f1 = 0.0
            if (result.perf_precision + result.perf_recall) > 0:
                f1 = (
                    2 * result.perf_precision * result.perf_recall
                    / (result.perf_precision + result.perf_recall)
                )
            push_model_eval("current", result.perf_precision, result.perf_recall, f1)
    except ImportError:
        pass
    except Exception as exc:
        print(f"[drift_detector] WARNING: pushgateway push failed — {exc}")


def main():
    parser = argparse.ArgumentParser(
        description="Detect data drift và performance drift giữa 2 CSVs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--reference", required=True, help="Path to reference (baseline) CSV")
    parser.add_argument("--current", required=True, help="Path to current (production window) CSV")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
        help=f"Drift score threshold — fraction of features drifted (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--check-mode", choices=["data", "performance", "combined"], default="combined",
        help=(
            "data: chỉ Evidently DataDriftPreset (phát hiện data drift P(X) shift). "
            "performance: chỉ precision/recall trên labeled data (phát hiện concept drift P(Y|X) shift). "
            "combined: cả hai — cần thiết để không bỏ sót concept drift (Acceptance Criterion 4). "
            "Default: combined"
        ),
    )
    parser.add_argument(
        "--labeled-current", default=None,
        help="CSV với cột anomaly_label — bắt buộc cho performance/combined mode",
    )
    parser.add_argument(
        "--model-uri", default="models:/anomaly-detector@production",
        help="MLflow model URI cho performance evaluation",
    )
    parser.add_argument(
        "--perf-threshold", type=float, default=DEFAULT_PERF_THRESHOLD,
        help=f"Minimum acceptable precision (default: {DEFAULT_PERF_THRESHOLD})",
    )
    parser.add_argument(
        "--log-mlflow", action="store_true", default=False,
        help="Log drift score và metrics lên MLflow tracking server",
    )
    args = parser.parse_args()

    ref_df = pd.read_csv(args.reference)
    cur_df = pd.read_csv(args.current)

    # ─── Data drift check ─────────────────────────────────────────────────────
    if args.check_mode in ("data", "combined"):
        result = detect_drift(ref_df, cur_df, threshold=args.threshold)
        print(f"[drift_detector] check_mode      : {args.check_mode}")
        print(f"[drift_detector] Drift score     : {result.score:.4f}")
        print(f"[drift_detector] Threshold       : {result.threshold}")
        print(f"[drift_detector] Drift detected  : {result.is_drift}")
        print(f"[drift_detector] Drifted features: {result.drifted_features}")
        print(f"[drift_detector] Report saved    : {result.report_path}")
    else:
        # performance-only mode: tạo stub DriftResult không có data drift info
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        result = DriftResult(
            score=0.0, is_drift=False, threshold=args.threshold,
            drifted_features=[], report_path="", timestamp=ts,
        )

    # ─── Performance (concept drift) check ───────────────────────────────────
    if args.check_mode in ("performance", "combined"):
        if not args.labeled_current:
            parser.error("--labeled-current là bắt buộc cho performance/combined mode")

        labeled_df = pd.read_csv(args.labeled_current)
        precision, recall, is_degraded = check_performance_drift(
            labeled_df, args.model_uri, perf_threshold=args.perf_threshold,
        )
        result.perf_precision = precision
        result.perf_recall = recall
        result.perf_is_degraded = is_degraded
        result.perf_threshold = args.perf_threshold

        # Output bắt buộc cho Acceptance Criterion 4:
        # phải in ra cả "Drift score" và "Perf precision" khi dùng combined mode
        print(f"[drift_detector] Perf precision  : {precision:.4f}  (threshold {args.perf_threshold})")
        print(f"[drift_detector] Perf recall     : {recall:.4f}")
        print(f"[drift_detector] Perf degraded   : {is_degraded}")

    # Combined drift: data drift HOẶC performance degradation
    any_drift = result.is_drift or result.perf_is_degraded

    # Log vào MLflow nếu được yêu cầu
    if args.log_mlflow:
        log_to_mlflow(result)

    # Push metrics lên Prometheus Pushgateway (no-op nếu không chạy)
    _push_metrics(result)

    # Exit code 1 nếu có drift — cho phép shell scripting
    raise SystemExit(1 if any_drift else 0)


if __name__ == "__main__":
    main()
