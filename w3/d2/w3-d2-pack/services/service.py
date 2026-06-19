#!/usr/bin/env python3
"""service.py — Generic mock microservice for W3-D2 chaos lab.

Mounted into every service container via docker-compose volume.
Reads config from environment variables:
    SERVICE_NAME    : service identifier (default: "unknown")
    BASE_LATENCY_MS : baseline response latency in ms (default: 50)
    JITTER_MS       : random ±jitter on latency (default: 10)
    FAIL_RATE       : fraction of requests that return 500 (default: 0.01)
    UPSTREAM_URL    : optional upstream to call on /chain (default: "")

Exposes:
    GET  /           → 200 {"service": ..., "status": "ok"}
    GET  /health     → 200 {"status": "healthy"} or 503
    GET  /metrics    → Prometheus text exposition (counter + histogram)
    GET  /chain      → call upstream and return combined result
    POST /inject     → runtime fault injection control (for testing)
"""
import os
import random
import time
import threading
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import (
    Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
)

# ── Config from env ───────────────────────────────────────────────────────────
SERVICE_NAME   = os.getenv("SERVICE_NAME", "unknown")
BASE_LATENCY   = int(os.getenv("BASE_LATENCY_MS", "50")) / 1000.0
JITTER         = int(os.getenv("JITTER_MS", "10")) / 1000.0
FAIL_RATE      = float(os.getenv("FAIL_RATE", "0.01"))
UPSTREAM_URL   = os.getenv("UPSTREAM_URL", "")

# ── Prometheus metrics ────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request duration in seconds",
    ["service"],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1.0, 2.5, 5.0, 10.0],
)
ERROR_COUNT = Counter(
    "http_errors_total",
    "Total HTTP 5xx errors",
    ["service"],
)
UP_GAUGE = Gauge(
    "service_up",
    "Service health (1=up, 0=down)",
    ["service"],
)
UP_GAUGE.labels(service=SERVICE_NAME).set(1)

# ── Runtime fault injection state ─────────────────────────────────────────────
_fault_lock = threading.Lock()
_injected_fault: dict[str, Any] = {}   # keys: type, extra_latency_ms, fail_rate_override


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title=SERVICE_NAME)


def _get_latency() -> float:
    base = BASE_LATENCY + random.uniform(-JITTER, JITTER)
    with _fault_lock:
        extra = _injected_fault.get("extra_latency_ms", 0) / 1000.0
    return max(0.001, base + extra)


def _should_fail() -> bool:
    with _fault_lock:
        override = _injected_fault.get("fail_rate_override")
    rate = override if override is not None else FAIL_RATE
    return random.random() < rate


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    status = str(response.status_code)
    REQUEST_COUNT.labels(
        service=SERVICE_NAME, method=request.method, status=status
    ).inc()
    REQUEST_LATENCY.labels(service=SERVICE_NAME).observe(duration)
    if response.status_code >= 500:
        ERROR_COUNT.labels(service=SERVICE_NAME).inc()
    return response


@app.get("/")
async def root():
    latency = _get_latency()
    time.sleep(latency)
    if _should_fail():
        UP_GAUGE.labels(service=SERVICE_NAME).set(0)
        return JSONResponse(
            {"service": SERVICE_NAME, "status": "error", "latency_ms": round(latency * 1000)},
            status_code=500,
        )
    UP_GAUGE.labels(service=SERVICE_NAME).set(1)
    return {"service": SERVICE_NAME, "status": "ok", "latency_ms": round(latency * 1000)}


@app.get("/health")
async def health():
    with _fault_lock:
        ftype = _injected_fault.get("type")
    if ftype == "health_fail":
        UP_GAUGE.labels(service=SERVICE_NAME).set(0)
        return JSONResponse({"status": "unhealthy", "service": SERVICE_NAME}, status_code=503)
    UP_GAUGE.labels(service=SERVICE_NAME).set(1)
    return {"status": "healthy", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


@app.get("/chain")
async def chain():
    """Call upstream service and return combined response."""
    latency = _get_latency()
    time.sleep(latency)
    if _should_fail():
        return JSONResponse(
            {"service": SERVICE_NAME, "status": "error"},
            status_code=500,
        )
    if UPSTREAM_URL:
        try:
            import urllib.request
            with urllib.request.urlopen(f"{UPSTREAM_URL}/health", timeout=2) as resp:
                upstream_status = resp.getcode()
        except Exception as exc:
            upstream_status = f"error: {exc}"
    else:
        upstream_status = "no upstream"
    return {
        "service": SERVICE_NAME,
        "upstream": UPSTREAM_URL,
        "upstream_status": upstream_status,
        "latency_ms": round(latency * 1000),
    }


@app.post("/inject")
async def inject_fault(payload: dict):
    """Runtime fault injection endpoint for testing.
    
    Payload examples:
        {"type": "latency", "extra_latency_ms": 500}
        {"type": "error", "fail_rate_override": 0.5}
        {"type": "health_fail"}
        {"type": "clear"}   ← removes all injected faults
    """
    with _fault_lock:
        if payload.get("type") == "clear":
            _injected_fault.clear()
        else:
            _injected_fault.update(payload)
    return {"service": SERVICE_NAME, "injected": _injected_fault}
