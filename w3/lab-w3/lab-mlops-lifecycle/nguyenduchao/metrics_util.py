"""
metrics_util.py — Push MLOps lifecycle metrics lên Prometheus Pushgateway.

Tất cả push calls là best-effort: pushgateway không chạy chỉ in warning, không raise exception.
URL: PUSHGATEWAY_URL env (default http://localhost:9091).

Usage (import trong drift_detector.py, retrain.py):
    from metrics_util import push_drift_score, push_model_eval, push_event, push_active_version
"""

import os

PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "http://localhost:9091")


def _registry():
    """Tạo CollectorRegistry mới mỗi lần push để tránh duplicate metric issues."""
    try:
        from prometheus_client import CollectorRegistry
        return CollectorRegistry()
    except ImportError:
        return None


def push_drift_score(score: float, threshold: float) -> None:
    """Push drift score hiện tại và threshold lên pushgateway (job=drift_detector)."""
    try:
        from prometheus_client import Gauge, push_to_gateway
        reg = _registry()
        if reg is None:
            return
        g_score = Gauge("mlops_drift_score", "Fraction of features drifted (0-1)", registry=reg)
        g_thresh = Gauge("mlops_drift_threshold", "Configured drift threshold", registry=reg)
        g_flag = Gauge("mlops_drift_is_drift", "1 if drift detected, 0 otherwise", registry=reg)
        g_score.set(score)
        g_thresh.set(threshold)
        g_flag.set(1.0 if score > threshold else 0.0)
        push_to_gateway(PUSHGATEWAY_URL, job="drift_detector", registry=reg)
    except ImportError:
        print("[metrics_util] WARNING: prometheus_client not installed")
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_model_eval(version: str, precision: float, recall: float, f1: float) -> None:
    """Push per-version precision/recall/f1 lên pushgateway (job=retrain)."""
    try:
        from prometheus_client import Gauge, push_to_gateway
        reg = _registry()
        if reg is None:
            return
        g_p = Gauge("mlops_model_precision", "Model precision on eval set", ["version"], registry=reg)
        g_r = Gauge("mlops_model_recall", "Model recall on eval set", ["version"], registry=reg)
        g_f = Gauge("mlops_model_f1", "Model F1 on eval set", ["version"], registry=reg)
        g_p.labels(version=version).set(precision)
        g_r.labels(version=version).set(recall)
        g_f.labels(version=version).set(f1)
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except ImportError:
        print("[metrics_util] WARNING: prometheus_client not installed")
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_event(event_type: str, version: str) -> None:
    """
    Increment lifecycle event counter lên pushgateway (job=retrain).

    event_type: 'retrain_triggered' | 'auto_rollback_v2_to_v1'
    """
    try:
        from prometheus_client import Counter, push_to_gateway
        reg = _registry()
        if reg is None:
            return
        c = Counter(
            f"mlops_{event_type}_total",
            f"Total count of {event_type} events",
            ["version"],
            registry=reg,
        )
        c.labels(version=version).inc()
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except ImportError:
        print("[metrics_util] WARNING: prometheus_client not installed")
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")


def push_active_version(version: str, alias: str) -> None:
    """Push version number và alias mapping lên pushgateway (job=retrain)."""
    try:
        from prometheus_client import Gauge, push_to_gateway
        reg = _registry()
        if reg is None:
            return
        g_num = Gauge(
            "mlops_active_version_number",
            "Integer version number for the given alias",
            ["alias"],
            registry=reg,
        )
        g_info = Gauge(
            "mlops_active_version_info",
            "Label-only gauge: alias→version mapping (value always 1)",
            ["alias", "version"],
            registry=reg,
        )
        try:
            g_num.labels(alias=alias).set(int(version))
        except (ValueError, TypeError):
            g_num.labels(alias=alias).set(0)
        g_info.labels(alias=alias, version=str(version)).set(1)
        push_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except ImportError:
        print("[metrics_util] WARNING: prometheus_client not installed")
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")
