"""
serve.py — AIOps W2-D3: Model Serving
FastAPI HTTP service wrapping the full AIOps pipeline (correlation + RCA).

Run:
    uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 1 --reload

Endpoints:
    GET  /healthz   — liveness probe
    GET  /readyz    — readiness probe (checks graph + history)
    GET  /version   — app + pipeline config info
    POST /incident  — main pipeline endpoint
    GET  /metrics   — Prometheus metrics scrape

Environment variables:
    AIOPS_USE_LLM   — "true"|"false"  (default true; false = graph-only, kill switch)
    AIOPS_GAP_SEC   — override gap_sec (default 120)
    AIOPS_MAX_HOP   — override max_hop (default 2)
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from prometheus_client import (
    Counter, Histogram, make_asgi_app, REGISTRY
)

# ── Logging setup (JSON formatter) ────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "extra", {})
        payload: dict = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "msg":    record.getMessage(),
            "logger": record.name,
        }
        payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_setup_logging()
logger = logging.getLogger("aiops.serve")

# ── Load pipeline (module-level — cached per worker) ──────────────────────────
from pipeline import GRAPH, HISTORY, process_batch   # noqa: E402

APP_VERSION = "1.0.0"
GRAPH_VERSION = "g-manual-20260613"
GRAPH_LOADED_AT = datetime.now(tz=timezone.utc).isoformat()

# ── Config from env ────────────────────────────────────────────────────────────
USE_LLM  = os.getenv("AIOPS_USE_LLM", "true").lower() != "false"
GAP_SEC  = int(os.getenv("AIOPS_GAP_SEC", "120"))
MAX_HOP  = int(os.getenv("AIOPS_MAX_HOP", "2"))

# ── Prometheus metrics ─────────────────────────────────────────────────────────
REQUEST_COUNTER = Counter(
    "aiops_incident_requests_total",
    "Total /incident requests",
    ["status"],
)
REQUEST_LATENCY = Histogram(
    "aiops_incident_latency_seconds",
    "End-to-end /incident latency in seconds",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)
LLM_FAILURES = Counter(
    "aiops_llm_failures_total",
    "LLM call failures",
    ["reason"],
)
CLUSTERS_PER_REQUEST = Histogram(
    "aiops_clusters_per_request",
    "Number of clusters returned per /incident request",
    buckets=[0, 1, 2, 5, 10, 20, 50],
)

# ── FastAPI app ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AIOps Incident Pipeline",
    version=APP_VERSION,
    description="HTTP service: POST batch alerts → incident report (correlation + RCA)",
)

# Mount Prometheus metrics endpoint
app.mount("/metrics", make_asgi_app())


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class AlertIn(BaseModel):
    id:        str
    ts:        str                           # ISO-8601 UTC
    service:   str
    metric:    str
    severity:  str                           # warn | crit | high | info
    value:     float
    threshold: float
    labels:    dict[str, str] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def sev_valid(cls, v: str) -> str:
        allowed = {"warn", "crit", "high", "info", "low", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {allowed}, got '{v}'")
        return v.lower()


class IncidentRequest(BaseModel):
    alerts: list[AlertIn]


class RootCauseOut(BaseModel):
    service:         str
    root_cause_class: str = Field(alias="class", default="unknown")
    confidence:      float
    graph_top3:      list[list[Any]] = Field(default_factory=list)
    reasoning:       str             = ""
    method:          str             = "graph+retrieval"
    auto_remediate:  bool            = False

    model_config = {"populate_by_name": True}


class ClusterOut(BaseModel):
    cluster_id:   str
    alert_count:  int
    services:     list[str]
    time_range:   list[str]
    max_severity: str
    fingerprints: list[str] = Field(default_factory=list)


class IncidentResponse(BaseModel):
    clusters:             list[ClusterOut]
    root_cause:           dict[str, Any]
    recommended_actions:  list[str]
    similar_incidents:    list[str]
    processing_ms:        float = 0.0


# ── Latency middleware ─────────────────────────────────────────────────────────

@app.middleware("http")
async def latency_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)
    response.headers["X-Response-Time-Ms"] = str(duration_ms)
    logger.info(
        "%s %s %d",
        request.method, request.url.path, response.status_code,
        extra={"extra": {"duration_ms": duration_ms,
                         "method": request.method,
                         "path": request.url.path,
                         "status": response.status_code}},
    )
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/healthz", tags=["ops"])
async def healthz():
    """Liveness probe — always 200 if process is alive."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz():
    """
    Readiness probe — checks that pipeline state is loaded.
    Fails with 503 if graph or history is empty.
    Note: LLM availability is NOT checked here — LLM down should not
    mark the pod unready (graceful degradation to graph-only mode).
    """
    checks: dict[str, Any] = {
        "graph":   GRAPH.number_of_nodes() > 0,
        "history": len(HISTORY) > 0,
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ready", "checks": checks}


@app.get("/version", tags=["ops"])
async def version():
    """Pipeline config + graph metadata for debugging regressions."""
    return {
        "app":              APP_VERSION,
        "graph_version":    GRAPH_VERSION,
        "graph_loaded_at":  GRAPH_LOADED_AT,
        "graph_source":     "manual-services.json",
        "graph_node_count": GRAPH.number_of_nodes(),
        "graph_edge_count": GRAPH.number_of_edges(),
        "pipeline_config": {
            "gap_sec":    GAP_SEC,
            "max_hop":    MAX_HOP,
            "use_llm":    USE_LLM,
            "rca_method": "graph+retrieval",
            "llm_model":  "disabled" if not USE_LLM else "graph-only-simulated",
        },
    }


@app.post("/incident", response_model=IncidentResponse, tags=["pipeline"])
async def incident(request: IncidentRequest):
    """
    Main pipeline endpoint.
    Accepts a batch of alerts, returns incident report.

    - Empty alerts list → 400
    - Pipeline error     → 500 (stack trace is logged, NOT leaked to client)
    """
    if not request.alerts:
        raise HTTPException(status_code=400, detail="alerts list must not be empty")

    alerts_dicts = [a.model_dump() for a in request.alerts]

    t0 = time.perf_counter()
    try:
        result = process_batch(alerts_dicts)
        REQUEST_COUNTER.labels(status="success").inc()
        CLUSTERS_PER_REQUEST.observe(len(result.get("clusters", [])))
    except Exception:
        REQUEST_COUNTER.labels(status="error").inc()
        logger.error("Pipeline failed", exc_info=True,
                     extra={"extra": {"alert_count": len(alerts_dicts)}})
        raise HTTPException(status_code=500, detail="Internal pipeline error. Check server logs.")

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    # Normalise root_cause dict keys for response
    rc = result.get("root_cause", {})
    rc_out = {
        "service":         rc.get("service", "unknown"),
        "class":           rc.get("class", "unknown"),
        "confidence":      rc.get("confidence", 0.0),
        "graph_top3":      rc.get("graph_top3", []),
        "reasoning":       rc.get("reasoning", ""),
        "method":          rc.get("method", "graph+retrieval"),
        "auto_remediate":  rc.get("auto_remediate", False),
    }

    logger.info(
        "Incident processed",
        extra={"extra": {
            "alert_count":   len(alerts_dicts),
            "cluster_count": len(result.get("clusters", [])),
            "root_cause":    rc_out["service"],
            "confidence":    rc_out["confidence"],
            "duration_ms":   elapsed_ms,
        }},
    )

    return IncidentResponse(
        clusters=result.get("clusters", []),
        root_cause=rc_out,
        recommended_actions=result.get("recommended_actions", []),
        similar_incidents=result.get("similar_incidents", []),
        processing_ms=elapsed_ms,
    )
