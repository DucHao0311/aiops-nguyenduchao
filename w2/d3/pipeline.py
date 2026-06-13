"""
pipeline.py — AIOps W2-D3 Glue Layer
Wires Alert Correlation (W2-D1) + RCA (W2-D2) into a single process_batch() call.

Design decisions:
- GRAPH and HISTORY are module-level caches loaded once at import time.
  Single-worker deployment → no cross-process cache issue.
- All notebook logic is extracted into pure functions that accept explicit
  parameters (no implicit module-level state), making them testable + reusable.
- CRIT_WEIGHT and SEV_MAP are module-level constants (not mutable).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

# ── Paths (relative to this file) ─────────────────────────────────────────────
_HERE = Path(__file__).parent
SERVICES_PATH  = _HERE.parent / "d1" / "dataset" / "services.json"
INCIDENTS_PATH = _HERE.parent / "d2" / "dataset" / "incidents_history.json"

# ── Hyperparameters ────────────────────────────────────────────────────────────
DEFAULT_GAP_SEC = 120   # tighter than D1 (300s) for production responsiveness
DEFAULT_MAX_HOP = 2     # per assignment spec §2.1

CRIT_WEIGHT = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
SEV_MAP_NUM  = {"crit": 3, "high": 2, "warn": 1, "info": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level initialisation — runs once per worker process
# ─────────────────────────────────────────────────────────────────────────────

def _load_graph(path: Path) -> nx.DiGraph:
    """Build a NetworkX DiGraph from services.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for svc in data["services"]:
        G.add_node(svc["name"], tier=svc["tier"],
                   criticality=svc["criticality"], team=svc["team"])
    for store in data["stores"]:
        G.add_node(store["name"], tier="store",
                   criticality=store["criticality"], team=store["team"])
    for edge in data["edges"]:
        G.add_edge(edge["from"], edge["to"], type=edge.get("type", "http"))
    logger.info("Graph loaded: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    return G


def _load_incidents(path: Path) -> list[dict]:
    """Load historical incidents list from incidents_history.json."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    incidents = data["incidents"]
    logger.info("Incident history loaded: %d records", len(incidents))
    return incidents


# Global caches — populated at import time so every request reuses them
GRAPH: nx.DiGraph = _load_graph(SERVICES_PATH)
HISTORY: list[dict] = _load_incidents(INCIDENTS_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Alert Correlation  (extracted from W2-D1 notebook)
# ─────────────────────────────────────────────────────────────────────────────

def make_fingerprint(alert: dict) -> str:
    """service|metric|severity — excludes ts and value intentionally."""
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


def build_adjacency(G: nx.DiGraph) -> dict[str, set]:
    """Undirected adjacency map derived from the directed service graph."""
    adj: dict[str, set] = defaultdict(set)
    for src, dst in G.edges():
        adj[src].add(dst)
        adj[dst].add(src)
    return dict(adj)


def _get_neighbors(service: str, adj: dict, max_hop: int) -> set:
    visited, frontier = {service}, {service}
    for _ in range(max_hop):
        nxt = set()
        for n in frontier:
            for nb in adj.get(n, set()):
                if nb not in visited:
                    nxt.add(nb); visited.add(nb)
        frontier = nxt
    return visited - {service}


def _are_topo_related(svcs_a: set, svcs_b: set, adj: dict, max_hop: int) -> bool:
    for svc in svcs_a:
        reachable = _get_neighbors(svc, adj, max_hop) | {svc}
        if reachable & svcs_b:
            return True
    return False


def _ts_epoch(ts_str: str) -> float:
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()


def _epoch_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def correlate(alerts: list[dict],
              G: nx.DiGraph,
              gap_sec: int = DEFAULT_GAP_SEC,
              max_hop: int = DEFAULT_MAX_HOP) -> list[dict]:
    """
    Two-phase alert correlation:
      Phase 1 — per-fingerprint temporal deduplication (gap_sec window)
      Phase 2 — topology merge (max_hop BFS)

    Returns list of cluster dicts matching cluster_summary.json schema.
    """
    if not alerts:
        return []

    adj = build_adjacency(G)

    # Attach fingerprints
    for a in alerts:
        a.setdefault("fingerprint", make_fingerprint(a))

    # ── Phase 1 ──
    fp_windows: dict[str, dict] = {}
    windows: list[dict] = []

    for alert in sorted(alerts, key=lambda a: a["ts"]):
        fp = alert["fingerprint"]
        ts = _ts_epoch(alert["ts"])
        if fp not in fp_windows or ts - fp_windows[fp]["t_end"] > gap_sec:
            win = {"_alerts": [alert], "_fps": {fp},
                   "services": {alert["service"]},
                   "t_start": ts, "t_end": ts}
            fp_windows[fp] = win
            windows.append(win)
        else:
            fp_windows[fp]["t_end"] = ts
            fp_windows[fp]["_alerts"].append(alert)

    # ── Phase 2 ──
    merged = True
    while merged:
        merged = False
        used = [False] * len(windows)
        new_windows = []
        for i in range(len(windows)):
            if used[i]: continue
            base = windows[i]
            for j in range(i + 1, len(windows)):
                if used[j]: continue
                cand = windows[j]
                close = not (base["t_end"] + gap_sec < cand["t_start"] or
                              cand["t_end"] + gap_sec < base["t_start"])
                if close and _are_topo_related(base["services"], cand["services"],
                                               adj, max_hop):
                    base["_alerts"].extend(cand["_alerts"])
                    base["_fps"]     |= cand["_fps"]
                    base["services"] |= cand["services"]
                    base["t_start"] = min(base["t_start"], cand["t_start"])
                    base["t_end"]   = max(base["t_end"],   cand["t_end"])
                    used[j] = True
                    merged  = True
            new_windows.append(base)
        windows = new_windows

    # ── Serialise ──
    sev_order = {"info": 0, "warn": 1, "high": 2, "crit": 3}
    clusters = []
    for idx, win in enumerate(sorted(windows, key=lambda w: w["t_start"])):
        max_sev = max(win["_alerts"], key=lambda a: sev_order.get(a["severity"], 0))["severity"]
        clusters.append({
            "cluster_id":   f"c-{idx+1:03d}-{idx:03d}",
            "alert_count":  len(win["_alerts"]),
            "services":     sorted(win["services"]),
            "time_range":   [_epoch_iso(win["t_start"]), _epoch_iso(win["t_end"])],
            "max_severity": max_sev,
            "fingerprints": sorted(win["_fps"]),
        })
    return clusters


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Root Cause Analysis  (extracted from W2-D2 notebook)
# ─────────────────────────────────────────────────────────────────────────────

def graph_score(G: nx.DiGraph, cluster: dict, alerts: list[dict]) -> list[tuple[str, float]]:
    """
    Graph traversal + temporal scorer.
    Accepts explicit `alerts` list (no implicit global dependency).
    Returns sorted list of (service, score) tuples, highest first.
    """
    cluster_services = set(cluster["services"])
    cluster_alerts   = [a for a in alerts if a["service"] in cluster_services]

    first_seen: dict[str, datetime] = {}
    for a in cluster_alerts:
        svc = a["service"]
        ts  = datetime.fromisoformat(a["ts"].replace("Z", "+00:00"))
        if svc not in first_seen or ts < first_seen[svc]:
            first_seen[svc] = ts

    alert_count   = Counter(a["service"] for a in cluster_alerts)
    max_sev_svc: dict[str, int] = {}
    for a in cluster_alerts:
        s = SEV_MAP_NUM.get(a["severity"], 0)
        max_sev_svc[a["service"]] = max(max_sev_svc.get(a["service"], 0), s)

    if not first_seen:
        return [(svc, 0.5) for svc in cluster_services if svc in G]

    times   = list(first_seen.values())
    t_min, t_max = min(times), max(times)
    t_range = max((t_max - t_min).total_seconds(), 1)

    scores: dict[str, float] = {}
    for svc in cluster_services:
        if svc not in G:
            continue
        node_data = G.nodes.get(svc, {})
        tier      = node_data.get("tier", "api")
        in_degree = G.in_degree(svc)

        temporal = (1.0 - (first_seen[svc] - t_min).total_seconds() / t_range * 0.8
                    if svc in first_seen else 0.1)
        upstream_score  = 0.2 if tier == "edge" else 1.0 / (1.0 + in_degree)
        descendants_in  = [n for n in nx.descendants(G, svc) if n in cluster_services]
        downstream_score= min(len(descendants_in) / max(len(cluster_services), 1), 1.0)
        crit            = CRIT_WEIGHT.get(node_data.get("criticality", "medium"), 0.5)
        total_alerts    = sum(alert_count.values()) or 1
        density         = alert_count.get(svc, 0) / total_alerts
        max_s           = max_sev_svc.get(svc, 0) / 3.0

        scores[svc] = round(
            0.30*temporal + 0.20*upstream_score + 0.20*downstream_score
            + 0.15*crit + 0.10*density + 0.05*max_s, 4
        )
    return sorted(scores.items(), key=lambda x: -x[1])


def _keyword_sim(query_services: set, query_fps: list, incident: dict) -> float:
    inc_services = set(incident["services_involved"])
    union        = query_services | inc_services
    jaccard      = len(query_services & inc_services) / len(union) if union else 0.0

    query_kw = set()
    for fp in query_fps:
        query_kw.update(re.split(r"[|_\-\s]+", fp.lower()))
    for s in query_services:
        query_kw.update(s.lower().replace("-", " ").split())
    query_kw.discard("")

    summary_words = set(re.split(r"[\s\.,;:\-]+", incident["summary"].lower()))
    summary_words.update(re.split(r"[\s\.,;:\-]+", incident["root_cause_class"].lower()))
    kw_match = len(query_kw & summary_words) / max(len(query_kw), 1)

    sev_w   = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
    sev_score = sev_w.get(incident["severity"], 0.5)
    return round(0.5*jaccard + 0.35*kw_match + 0.15*sev_score, 4)


def retrieve_top_k(cluster: dict, incidents: list[dict], k: int = 3) -> list[tuple[dict, float]]:
    """kNN-style keyword retrieval — top-K similar historical incidents."""
    query_services = set(cluster["services"])
    query_fps      = cluster.get("fingerprints", [])
    scored = [(inc, _keyword_sim(query_services, query_fps, inc)) for inc in incidents]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def classify_from_retrieval(top_k: list[tuple[dict, float]],
                             graph_top: list[tuple[str, float]]) -> dict:
    """kNN classifier — class and actions from top-1 similar incident."""
    if not top_k:
        return {"class": "unknown", "actions": ["Manual investigation required"],
                "confidence": 0.10, "source_incident": None}
    top_inc, top_sim = top_k[0]
    graph_conf = graph_top[0][1] if graph_top else 0.5
    confidence = round(max(0.10, min(0.97, math.sqrt(top_sim * graph_conf))), 4)
    remediation = top_inc.get("remediation", "")
    actions = [a.strip() for a in re.split(r"\. |; ", remediation) if a.strip()]
    return {
        "class":           top_inc["root_cause_class"],
        "actions":         actions[:4],
        "confidence":      confidence,
        "source_incident": top_inc["id"],
    }


def run_rca(cluster: dict, alerts: list[dict],
            G: nx.DiGraph, history: list[dict]) -> dict:
    """
    Full RCA for a single cluster.
    Returns dict compatible with IncidentResponse.root_cause schema.
    """
    g_top  = graph_score(G, cluster, alerts)
    top_k  = retrieve_top_k(cluster, history, k=3)
    clf    = classify_from_retrieval(top_k, g_top)

    root_cause = g_top[0][0] if g_top else (cluster["services"][0] if cluster["services"] else "unknown")
    graph_top3 = [[svc, sc] for svc, sc in g_top[:3]]
    while len(graph_top3) < 3:
        graph_top3.append(["N/A", 0.0])

    in_deg   = G.in_degree(root_cause) if root_cause in G else "N/A"
    top_id   = top_k[0][0]["id"] if top_k else "none"
    top_sim  = round(top_k[0][1], 3) if top_k else 0
    top_sum  = top_k[0][0]["summary"][:90] if top_k else ""
    reasoning = (
        f"Graph traversal: '{root_cause}' top candidate (score={g_top[0][1] if g_top else 0:.3f}), "
        f"in-degree={in_deg}. "
        f"kNN top-1: '{top_id}' (sim={top_sim}): {top_sum}... "
        f"Class: '{clf['class']}', confidence={clf['confidence']}."
    )

    return {
        "root_cause":        root_cause,
        "root_cause_class":  clf["class"],
        "confidence":        clf["confidence"],
        "graph_top3":        graph_top3,
        "reasoning":         reasoning,
        "actions":           clf["actions"],
        "similar_incidents": [inc["id"] for inc, _ in top_k],
        "method":            "graph+retrieval",
        "auto_remediate":    clf["confidence"] >= 0.82,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — called by serve.py
# ─────────────────────────────────────────────────────────────────────────────

def process_batch(alerts: list[dict]) -> dict:
    """
    Full pipeline: alerts → clusters → RCA → IncidentResponse-compatible dict.

    Returns:
        {
          "clusters": [...],
          "root_cause": {...},
          "recommended_actions": [...],
          "similar_incidents": [...],
        }
    """
    # Layer 1 — correlation
    clusters = correlate(alerts, GRAPH, gap_sec=DEFAULT_GAP_SEC, max_hop=DEFAULT_MAX_HOP)

    if not clusters:
        return {
            "clusters": [],
            "root_cause": {"service": "unknown", "class": "unknown",
                           "confidence": 0.0, "reasoning": "No clusters produced"},
            "recommended_actions": ["Manual investigation required"],
            "similar_incidents":   [],
        }

    # Layer 2 — RCA on largest cluster
    primary = max(clusters, key=lambda c: c["alert_count"])
    rca     = run_rca(primary, alerts, GRAPH, HISTORY)

    # Pack response
    return {
        "clusters":            clusters,
        "root_cause": {
            "service":    rca["root_cause"],
            "class":      rca["root_cause_class"],
            "confidence": rca["confidence"],
            "graph_top3": rca["graph_top3"],
            "reasoning":  rca["reasoning"],
            "method":     rca["method"],
            "auto_remediate": rca["auto_remediate"],
        },
        "recommended_actions": rca["actions"],
        "similar_incidents":   rca["similar_incidents"],
    }
