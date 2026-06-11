#!/usr/bin/env python3
"""Multi-signal RCA engine combining PageRank + metric drift earliness + correlation.

Real prod RCA stacks (BARO, NSigma, TopoWalk) combine multiple rankers via Weighted RRF.
This implements a simplified 3-ranker fusion as teaching baseline:

  R1. PageRank on reverse topology, personalized by alerting services
  R2. Earliest-drift ranker — service whose key metrics drifted earliest wins
  R3. Correlation ranker — service whose drift CORRELATES with alerts wins

Weighted RRF: score(s) = sum_r w_r / (k + rank_r(s)), k=60, w = [0.3, 0.5, 0.2]

Usage:
    uv run python models/rca-engine.py --scenario S06
"""
from __future__ import annotations
import argparse, json, sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np
import networkx as nx

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "workshop.db"


def load_scenario_data(sid: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    metrics = conn.execute(
        "SELECT timestamp, service, metric, value FROM metrics WHERE scenario = ? ORDER BY timestamp",
        (sid,)).fetchall()
    alerts = conn.execute("SELECT * FROM alerts WHERE scenario = ? ORDER BY opened_at",
                          (sid,)).fetchall()
    topology = conn.execute("SELECT src_service, dst_service FROM topology").fetchall()
    sc = conn.execute("SELECT full_json FROM scenarios WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return [dict(m) for m in metrics], [dict(a) for a in alerts], [(t["src_service"], t["dst_service"]) for t in topology], json.loads(sc["full_json"])


def pagerank_ranker(alerts, topology):
    G = nx.DiGraph()
    for s, d in topology: G.add_edge(s, d)
    Gr = G.reverse()
    alerting = {a["service"] for a in alerts}
    pers = {n: 1.0 for n in Gr.nodes()}
    for s in alerting:
        if s in pers: pers[s] = 10.0
    try:
        pr = nx.pagerank(Gr, personalization=pers, max_iter=100)
    except Exception:
        pr = {n: 0 for n in Gr.nodes()}
    return sorted(pr.items(), key=lambda kv: -kv[1])


def earliest_drift_ranker(metrics):
    """Per service, find earliest timestamp where any metric deviates > 3σ from baseline."""
    # Build per (svc, metric) baseline (first 60 samples) and find first drift
    by_pair: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for m in metrics:
        by_pair[(m["service"], m["metric"])].append((m["timestamp"], m["value"]))
    earliest_per_svc: dict[str, str] = {}
    for (svc, metric), pts in by_pair.items():
        pts.sort()
        if len(pts) < 70: continue
        baseline = [v for _, v in pts[:60]]
        mu, sigma = np.mean(baseline), max(np.std(baseline), 1e-6)
        for ts, v in pts[60:]:
            if abs(v - mu) > 3 * sigma:
                if svc not in earliest_per_svc or ts < earliest_per_svc[svc]:
                    earliest_per_svc[svc] = ts
                break
    # Earlier drift = higher rank
    ranked = sorted(earliest_per_svc.items(), key=lambda kv: kv[1])
    # Convert ts to a score: 1.0 / (1 + offset_from_earliest_in_min)
    if not ranked: return []
    base_ts = ranked[0][1]
    from datetime import datetime
    def to_dt(s): return datetime.fromisoformat(s)
    base = to_dt(base_ts)
    return [(svc, 1.0 / (1 + (to_dt(ts) - base).total_seconds() / 60)) for svc, ts in ranked]


def correlation_ranker(metrics, alerts):
    """For each alerting service, compute correlation between its metric drift and alert phase markers.
    Service whose metrics co-vary most with alert timestamps ranks highest.
    Simplified: count metrics that drifted in the same hour as alerts for that service.
    """
    alert_services = {a["service"] for a in alerts}
    by_svc: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    for m in metrics:
        if m["service"] in alert_services or m["service"] not in {a["service"] for a in alerts}:
            by_svc[m["service"]].append((m["timestamp"], m["metric"], m["value"]))
    # Rank: total number of distinct metrics that drifted (proxy for "involved in cascade")
    drift_count: dict[str, int] = {}
    for svc, pts in by_svc.items():
        per_metric: dict[str, list[float]] = defaultdict(list)
        for _, mt, v in pts:
            per_metric[mt].append(v)
        cnt = 0
        for mt, vals in per_metric.items():
            if len(vals) < 70: continue
            baseline_mu = np.mean(vals[:60])
            baseline_sigma = max(np.std(vals[:60]), 1e-6)
            tail_max = max(abs(v - baseline_mu) for v in vals[60:])
            if tail_max > 3 * baseline_sigma:
                cnt += 1
        drift_count[svc] = cnt
    return sorted(drift_count.items(), key=lambda kv: -kv[1])


def weighted_rrf(rankings: list[list[tuple]], weights: list[float], k: int = 60) -> list[tuple[str, float]]:
    score: dict[str, float] = defaultdict(float)
    for r, w in zip(rankings, weights):
        for i, (svc, _) in enumerate(r):
            score[svc] += w / (k + i + 1)
    return sorted(score.items(), key=lambda kv: -kv[1])


def run_rca(sid: str) -> dict:
    metrics, alerts, topology, sc = load_scenario_data(sid)
    r1 = pagerank_ranker(alerts, topology)
    r2 = earliest_drift_ranker(metrics)
    r3 = correlation_ranker(metrics, alerts)
    fused = weighted_rrf([r1, r2, r3], weights=[0.3, 0.5, 0.2])
    return {
        "scenario": sid,
        "expected": sc.get("expected_rca", {}),
        "rankers": {
            "pagerank_top5": [{"service": s, "score": round(v, 4)} for s, v in r1[:5]],
            "earliest_drift_top5": [{"service": s, "score": round(v, 4)} for s, v in r2[:5]],
            "correlation_top5": [{"service": s, "score": v} for s, v in r3[:5]],
        },
        "fused_top5": [{"service": s, "score": round(v, 4)} for s, v in fused[:5]],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="S06")
    args = p.parse_args()
    result = run_rca(args.scenario)
    print(json.dumps(result, indent=2))
    print(f"\n>> Expected root: {result['expected'].get('top_service')}")
    print(f">> Fused #1:     {result['fused_top5'][0]['service']}")
    print(f">> Match: {result['expected'].get('top_service') == result['fused_top5'][0]['service']}")


if __name__ == "__main__":
    main()
