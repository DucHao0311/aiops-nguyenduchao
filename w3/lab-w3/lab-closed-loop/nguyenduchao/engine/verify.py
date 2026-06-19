"""
engine/verify.py — Prometheus-based post-action verification.

verify_service():
  Polls Prometheus every poll_interval_s for up to timeout_s seconds.
  Requires min_samples consecutive "healthy" reads before returning True.
  A healthy read = p99 latency < latency_p99_max_ms AND up == up_required.
"""

import time

import requests

from engine.logger import JsonLogger

log = JsonLogger("verify")


def query_prometheus(prometheus_url: str, promql: str) -> float | None:
    """Instant query against Prometheus.  Returns the scalar float or None on error."""
    try:
        resp = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": promql},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        results = data.get("result", [])
        if results:
            return float(results[0]["value"][1])
        return None
    except Exception as exc:
        log.error("PROMETHEUS_QUERY_ERROR", query=promql, error=str(exc))
        return None


def verify_service(
    prometheus_url: str,
    service: str,
    baseline: dict,
    timeout_s: int,
    poll_interval_s: int,
    min_samples: int,
) -> bool:
    """Poll Prometheus until the service is healthy or timeout expires.

    Returns True only if min_samples consecutive polls all pass.
    Resets the consecutive counter on any unhealthy sample.
    """
    thresholds = baseline["verify_thresholds"]
    queries = baseline["prometheus_queries"]

    latency_q = queries["latency_p99"].replace("{service}", service)
    up_q = queries["up"].replace("{service}", service)

    latency_max = thresholds["latency_p99_max_ms"]
    up_required = thresholds["up_required"]

    deadline = time.time() + timeout_s
    consecutive_passes = 0
    total_samples = 0

    log.info(
        "VERIFY_START",
        service=service,
        timeout_s=timeout_s,
        min_samples=min_samples,
        latency_max_ms=latency_max,
    )

    while time.time() < deadline:
        latency = query_prometheus(prometheus_url, latency_q)
        up = query_prometheus(prometheus_url, up_q)
        total_samples += 1

        # latency metric comes back in ms (query multiplies by 1000)
        latency_ok = latency is not None and latency < latency_max
        up_ok = up is not None and up >= up_required

        log.info(
            "VERIFY_SAMPLE",
            service=service,
            sample=total_samples,
            latency_p99_ms=round(latency, 2) if latency is not None else None,
            up=up,
            latency_ok=latency_ok,
            up_ok=up_ok,
            consecutive_passes=consecutive_passes,
        )

        if latency_ok and up_ok:
            consecutive_passes += 1
            if consecutive_passes >= min_samples:
                log.info(
                    "VERIFY_PASS",
                    service=service,
                    total_samples=total_samples,
                    consecutive_passes=consecutive_passes,
                )
                return True
        else:
            consecutive_passes = 0  # must be consecutive

        time.sleep(poll_interval_s)

    log.warning(
        "VERIFY_FAIL",
        service=service,
        total_samples=total_samples,
        timeout_s=timeout_s,
    )
    return False
