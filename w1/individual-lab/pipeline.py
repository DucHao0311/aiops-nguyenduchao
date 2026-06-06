"""
AIOps W1 Individual Lab — Streaming Anomaly Pipeline
Author: Nguyen Duc Hao

Pipeline: nhận POST /ingest từ stream_generator → phát hiện anomaly → ghi alert vào alerts.jsonl
"""

from fastapi import FastAPI, Request
from collections import deque
import json
import uvicorn
from datetime import datetime, timezone

app = FastAPI()

ALERTS_FILE = "alerts.jsonl"

# ── Sliding window config ────────────────────────────────────────────────────
WINDOW_SIZE = 20          # Số ticks giữ trong window (mỗi tick = 30s prod time)
COOLDOWN_TICKS = 10       # Số ticks tối thiểu giữa 2 alert cùng loại (tránh spam)

# ── Thresholds ────────────────────────────────────────────────────────────────
# Memory leak: memory_usage_bytes tăng dần + GC pause tăng
MEMORY_UTIL_WARNING   = 0.75   # 75% limit → warning
MEMORY_UTIL_CRITICAL  = 0.85   # 85% limit → critical
MEMORY_SLOPE_WARN     = 30_000_000   # bytes tăng trung bình mỗi tick (warning)
MEMORY_SLOPE_CRIT     = 80_000_000   # bytes tăng trung bình mỗi tick (critical)
GC_PAUSE_WARN         = 50          # ms
GC_PAUSE_CRIT         = 100         # ms

# Traffic spike: RPS tăng đột biến + queue_depth cao
# Ngưỡng cao hơn để tránh false positive từ diurnal pattern
RPS_ZSCORE_WARN    = 3.5    # z-score so với window mean (tăng từ 2.5 → 3.5)
RPS_ZSCORE_CRIT    = 5.5    # (tăng từ 4.0 → 5.5)
QUEUE_DEPTH_WARN   = 50
QUEUE_DEPTH_CRIT   = 100

# Dependency timeout: upstream_timeout_rate cao + 5xx cao
UPSTREAM_TIMEOUT_WARN  = 5.0    # %
UPSTREAM_TIMEOUT_CRIT  = 20.0   # %
HTTP_5XX_WARN          = 3.0    # %
HTTP_5XX_CRIT          = 15.0   # %

# ── State ─────────────────────────────────────────────────────────────────────
window: deque = deque(maxlen=WINDOW_SIZE)
last_alert_tick: dict = {
    "memory_leak": -COOLDOWN_TICKS,
    "traffic_spike": -COOLDOWN_TICKS,
    "dependency_timeout": -COOLDOWN_TICKS,
}
tick_counter = 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    m = mean(values)
    variance = sum((x - m) ** 2 for x in values) / len(values)
    return variance ** 0.5 or 1.0


def write_alert(alert: dict):
    with open(ALERTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert) + "\n")
    print(f"[ALERT] {alert['severity'].upper()} | {alert['type']} | {alert['message']}")


def can_alert(fault_type: str) -> bool:
    return (tick_counter - last_alert_tick[fault_type]) >= COOLDOWN_TICKS


def record_alert(fault_type: str):
    last_alert_tick[fault_type] = tick_counter


# ── Detection logic ───────────────────────────────────────────────────────────

def detect_memory_leak(metrics: dict, timestamp: str, logs: list) -> None:
    """
    Phát hiện memory leak dựa trên:
    1. Memory utilization vượt ngưỡng
    2. Slope (xu hướng tăng) của memory trong sliding window
    3. GC pause tăng cao (tín hiệu phụ)
    """
    if len(window) < 5:
        return

    memory_util = metrics["memory_usage_bytes"] / metrics["memory_limit_bytes"]
    gc_pause = metrics["jvm_gc_pause_ms_avg"]

    # Tính slope: trung bình delta memory qua window
    mem_values = [w["memory_usage_bytes"] for w in window]
    mem_deltas = [mem_values[i+1] - mem_values[i] for i in range(len(mem_values)-1)]
    avg_slope = mean(mem_deltas) if mem_deltas else 0.0

    # Check log signals
    error_logs = [l for l in logs if l.get("level") in ("ERROR", "FATAL")]
    has_oom_log = any("OutOfMemory" in l.get("message", "") for l in logs)
    has_gc_log  = any("GC pause" in l.get("message", "") for l in logs)

    if not can_alert("memory_leak"):
        return

    severity = None
    evidence_parts = []

    if memory_util >= MEMORY_UTIL_CRITICAL or (avg_slope >= MEMORY_SLOPE_CRIT and gc_pause >= GC_PAUSE_WARN):
        severity = "critical"
    elif memory_util >= MEMORY_UTIL_WARNING or (avg_slope >= MEMORY_SLOPE_WARN and gc_pause >= GC_PAUSE_WARN):
        severity = "warning"
    elif has_oom_log or (has_gc_log and avg_slope > 0):
        severity = "warning"

    if severity:
        if memory_util > 0.5:
            evidence_parts.append(f"memory utilization at {memory_util*100:.1f}%")
        if avg_slope > 5_000_000:
            evidence_parts.append(f"avg growth {avg_slope/1e6:.1f}MB/tick")
        if gc_pause > GC_PAUSE_WARN:
            evidence_parts.append(f"GC pause {gc_pause:.0f}ms")
        if has_oom_log:
            evidence_parts.append("OOM warning in logs")

        message = "Memory leak detected: " + ", ".join(evidence_parts) if evidence_parts else \
                  f"Memory usage growing abnormally, utilization at {memory_util*100:.1f}%"

        write_alert({
            "timestamp": timestamp,
            "type": "memory_leak",
            "severity": severity,
            "message": message,
        })
        record_alert("memory_leak")


def detect_traffic_spike(metrics: dict, timestamp: str, logs: list) -> None:
    """
    Phát hiện traffic spike dựa trên:
    1. Z-score của RPS so với baseline window
    2. Queue depth vượt ngưỡng
    3. P99 latency tăng đột biến
    """
    if len(window) < 5:
        return

    rps = metrics["http_requests_per_sec"]
    queue = metrics["queue_depth"]
    latency = metrics["http_p99_latency_ms"]

    rps_history = [w["http_requests_per_sec"] for w in window]
    rps_mean = mean(rps_history)
    rps_std  = std(rps_history)
    rps_z    = (rps - rps_mean) / rps_std if rps_std > 0 else 0.0

    if not can_alert("traffic_spike"):
        return

    severity = None
    evidence_parts = []

    # Yêu cầu ít nhất 2 signal để tránh false positive từ diurnal pattern:
    # RPS z-score CAO + (queue cao HOẶC latency cao HOẶC 5xx cao)
    rps_5xx = metrics["http_5xx_rate"]
    multi_signal = (queue >= QUEUE_DEPTH_WARN) or (latency > 300) or (rps_5xx > HTTP_5XX_WARN)

    if queue >= QUEUE_DEPTH_CRIT or (rps_z >= RPS_ZSCORE_CRIT and multi_signal):
        severity = "critical"
    elif queue >= QUEUE_DEPTH_WARN or (rps_z >= RPS_ZSCORE_WARN and multi_signal):
        severity = "warning"

    # Tăng mức nếu latency cũng rất cao
    if severity == "warning" and latency > 500:
        severity = "critical"

    if severity:
        if rps_z >= RPS_ZSCORE_WARN:
            evidence_parts.append(f"RPS={rps:.0f} (z-score={rps_z:.1f}, baseline={rps_mean:.0f})")
        if queue >= QUEUE_DEPTH_WARN:
            evidence_parts.append(f"queue depth={queue}")
        if latency > 200:
            evidence_parts.append(f"P99 latency={latency:.0f}ms")

        message = "Traffic spike detected: " + ", ".join(evidence_parts) if evidence_parts else \
                  f"Abnormal traffic surge, RPS={rps:.0f} ({rps_z:+.1f}σ)"

        write_alert({
            "timestamp": timestamp,
            "type": "traffic_spike",
            "severity": severity,
            "message": message,
        })
        record_alert("traffic_spike")


def detect_dependency_timeout(metrics: dict, timestamp: str, logs: list) -> None:
    """
    Phát hiện dependency timeout dựa trên:
    1. upstream_timeout_rate vượt ngưỡng
    2. http_5xx_rate tăng đột biến
    3. P99 latency tăng (do retry storms)
    4. Logs circuit breaker / upstream timeout
    """
    if len(window) < 3:
        return

    timeout_rate = metrics["upstream_timeout_rate"]
    rate_5xx     = metrics["http_5xx_rate"]
    latency      = metrics["http_p99_latency_ms"]

    # Tính delta 5xx so với baseline
    rate_5xx_history = [w["http_5xx_rate"] for w in window]
    rate_5xx_baseline = mean(rate_5xx_history)

    has_circuit_breaker = any("circuit breaker" in l.get("message", "").lower() for l in logs)
    has_upstream_log    = any("timeout" in l.get("message", "").lower() for l in logs)

    if not can_alert("dependency_timeout"):
        return

    severity = None
    evidence_parts = []

    if timeout_rate >= UPSTREAM_TIMEOUT_CRIT or rate_5xx >= HTTP_5XX_CRIT:
        severity = "critical"
    elif timeout_rate >= UPSTREAM_TIMEOUT_WARN or rate_5xx >= HTTP_5XX_WARN:
        severity = "warning"
    elif has_circuit_breaker:
        severity = "critical"
    elif has_upstream_log and timeout_rate > 1.0:
        severity = "warning"

    if severity:
        if timeout_rate >= UPSTREAM_TIMEOUT_WARN:
            evidence_parts.append(f"upstream timeout rate={timeout_rate:.1f}%")
        if rate_5xx >= HTTP_5XX_WARN:
            evidence_parts.append(f"5xx rate={rate_5xx:.1f}% (baseline={rate_5xx_baseline:.1f}%)")
        if latency > 500:
            evidence_parts.append(f"P99 latency={latency:.0f}ms")
        if has_circuit_breaker:
            evidence_parts.append("circuit breaker OPEN in logs")

        message = "Dependency timeout detected: " + ", ".join(evidence_parts) if evidence_parts else \
                  f"Upstream dependency failing, timeout rate={timeout_rate:.1f}%"

        write_alert({
            "timestamp": timestamp,
            "type": "dependency_timeout",
            "severity": severity,
            "message": message,
        })
        record_alert("dependency_timeout")


# ── FastAPI endpoint ──────────────────────────────────────────────────────────

@app.post("/ingest")
async def ingest(request: Request):
    global tick_counter

    payload   = await request.json()
    metrics   = payload["metrics"]
    logs      = payload.get("logs", [])
    timestamp = payload["timestamp"]

    # Ghi vào sliding window
    window.append(metrics)
    tick_counter += 1

    # Chạy cả 3 detectors
    detect_memory_leak(metrics, timestamp, logs)
    detect_traffic_spike(metrics, timestamp, logs)
    detect_dependency_timeout(metrics, timestamp, logs)

    return {"status": "ok", "tick": tick_counter}


@app.get("/health")
async def health():
    return {"status": "healthy", "ticks_processed": tick_counter, "window_size": len(window)}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Tạo file alerts.jsonl nếu chưa có
    open(ALERTS_FILE, "a").close()
    print("[PIPELINE] Starting on http://0.0.0.0:8000")
    print(f"[PIPELINE] Alerts → {ALERTS_FILE}")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
