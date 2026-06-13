"""
tests/test_serve.py — Integration tests for AIOps serve.py

Run:
    pytest tests/ -v                   (from w2/d3/)

Coverage:
    1. GET /healthz → 200 + {"status": "ok"}
    2. POST /incident with empty alerts → 400
    3. POST /incident with invalid severity → 422 (Pydantic)
    4. POST /incident missing required field → 422
    5. POST /incident with 1 valid alert → 200, correct response shape
    6. POST /incident with full dataset (20 alerts) → 200, ≥1 cluster
    7. GET /readyz → 200 (graph + history loaded)
    8. GET /version → 200, expected keys present
    9. Unit: make_fingerprint excludes ts and value
   10. Unit: correlate produces ≥1 cluster on 20 alerts
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Make sure serve.py's directory is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from serve import app

client = TestClient(app)

# ── Fixtures ──────────────────────────────────────────────────────────────────

ALERT_VALID = {
    "id":        "test-001",
    "ts":        "2026-06-12T09:42:01Z",
    "service":   "payment-svc",
    "metric":    "db_connection_pool_used_ratio",
    "severity":  "crit",
    "value":     0.99,
    "threshold": 0.95,
    "labels":    {"env": "prod"},
}

# Load the real 20-alert dataset
_DATASET_PATH = Path(__file__).parent.parent.parent / "d1" / "dataset" / "alerts_sample.jsonl"

def _load_real_alerts() -> list[dict]:
    alerts = []
    with open(_DATASET_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                alerts.append(json.loads(line))
    return alerts


# ── Liveness / Readiness / Version ────────────────────────────────────────────

def test_healthz_200():
    """GET /healthz returns 200 and {"status": "ok"}."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_200():
    """GET /readyz returns 200 when graph and history are loaded."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["graph"] is True
    assert body["checks"]["history"] is True


def test_version_keys():
    """GET /version returns expected keys."""
    resp = client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("app", "graph_version", "graph_node_count", "pipeline_config"):
        assert key in body, f"Missing key: {key}"
    assert body["pipeline_config"]["gap_sec"] > 0


# ── Input validation ──────────────────────────────────────────────────────────

def test_incident_empty_alerts_400():
    """POST /incident with empty alerts → 400, not 500."""
    resp = client.post("/incident", json={"alerts": []})
    assert resp.status_code == 400


def test_incident_invalid_severity_422():
    """POST /incident with invalid severity → 422 (Pydantic validation)."""
    bad = dict(ALERT_VALID, severity="UNKNOWN_LEVEL")
    resp = client.post("/incident", json={"alerts": [bad]})
    assert resp.status_code == 422


def test_incident_missing_field_422():
    """POST /incident with missing required field 'ts' → 422."""
    incomplete = {k: v for k, v in ALERT_VALID.items() if k != "ts"}
    resp = client.post("/incident", json={"alerts": [incomplete]})
    assert resp.status_code == 422


def test_incident_missing_body_422():
    """POST /incident with no body → 422."""
    resp = client.post("/incident", json={})
    assert resp.status_code == 422


# ── Happy path ────────────────────────────────────────────────────────────────

def test_incident_single_alert_200():
    """POST /incident with 1 valid alert → 200, response has required fields."""
    resp = client.post("/incident", json={"alerts": [ALERT_VALID]})
    assert resp.status_code == 200
    body = resp.json()
    assert "clusters" in body
    assert "root_cause" in body
    assert "recommended_actions" in body
    assert "similar_incidents" in body
    assert isinstance(body["clusters"], list)
    assert isinstance(body["recommended_actions"], list)


def test_incident_full_dataset_200():
    """POST /incident with 20 real alerts → 200, ≥1 cluster, root_cause service present."""
    alerts = _load_real_alerts()
    resp = client.post("/incident", json={"alerts": alerts})
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert len(body["clusters"]) >= 1, "Expected at least 1 cluster"
    assert body["root_cause"]["service"] != ""
    assert body["root_cause"]["confidence"] > 0.0
    assert len(body["recommended_actions"]) >= 1


def test_incident_root_cause_fields():
    """Root cause dict has all required keys."""
    alerts = _load_real_alerts()
    resp = client.post("/incident", json={"alerts": alerts})
    rc = resp.json()["root_cause"]
    for key in ("service", "class", "confidence", "reasoning", "method"):
        assert key in rc, f"root_cause missing key: {key}"


def test_incident_cluster_shape():
    """Each cluster has required fields."""
    alerts = _load_real_alerts()
    resp = client.post("/incident", json={"alerts": alerts})
    for cluster in resp.json()["clusters"]:
        for key in ("cluster_id", "alert_count", "services", "time_range", "max_severity"):
            assert key in cluster, f"Cluster missing key: {key}"
        assert cluster["alert_count"] > 0
        assert len(cluster["services"]) > 0


def test_incident_processing_ms():
    """Response includes processing_ms timing field."""
    resp = client.post("/incident", json={"alerts": [ALERT_VALID]})
    assert resp.status_code == 200
    assert "processing_ms" in resp.json()
    assert resp.json()["processing_ms"] >= 0


def test_x_response_time_header():
    """X-Response-Time-Ms header is present in response."""
    resp = client.post("/incident", json={"alerts": [ALERT_VALID]})
    assert "x-response-time-ms" in {k.lower() for k in resp.headers}


# ── Unit tests (pure functions, no network) ───────────────────────────────────

from pipeline import make_fingerprint, correlate, GRAPH


def test_fingerprint_excludes_ts_and_value():
    """
    Two alerts differing only in ts and value must have identical fingerprints.
    This is the core deduplication invariant.
    """
    a1 = {**ALERT_VALID, "ts": "2026-06-12T09:42:01Z", "value": 0.85}
    a2 = {**ALERT_VALID, "ts": "2026-06-12T09:43:00Z", "value": 0.99}
    assert make_fingerprint(a1) == make_fingerprint(a2)


def test_fingerprint_differs_on_metric():
    """Alerts with different metrics must have different fingerprints."""
    a1 = dict(ALERT_VALID, metric="latency_p99_ms")
    a2 = dict(ALERT_VALID, metric="error_rate")
    assert make_fingerprint(a1) != make_fingerprint(a2)


def test_correlate_reduces_20_alerts():
    """correlate() on the 20-alert dataset must produce ≥1 and ≤20 clusters."""
    alerts = _load_real_alerts()
    clusters = correlate(alerts, GRAPH, gap_sec=120, max_hop=2)
    assert 1 <= len(clusters) <= 20


def test_correlate_cluster_schema():
    """Each cluster from correlate() has required keys."""
    alerts = _load_real_alerts()
    clusters = correlate(alerts, GRAPH, gap_sec=120, max_hop=2)
    for c in clusters:
        assert "cluster_id" in c
        assert "services" in c
        assert "fingerprints" in c
        assert len(c["services"]) > 0
