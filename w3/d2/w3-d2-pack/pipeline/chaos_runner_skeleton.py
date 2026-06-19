#!/usr/bin/env python3
"""chaos_runner.py skeleton — fill 2 TODO functions per §8.5.

Reads experiments.yaml, runs each entry: inject → measure → rollback → score.
Outputs chaos_results.json + stdout scoreboard.

USAGE:
    python chaos_runner.py [--experiments experiments.yaml] [--out chaos_results.json]
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml
import requests

PIPELINE_URL = "http://localhost:8000"
COOLDOWN_SECONDS = 120


def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]


def query_pipeline_alerts(since_ts: int) -> list[dict]:
    r = requests.get(f"{PIPELINE_URL}/alerts", params={"since": since_ts}, timeout=10)
    r.raise_for_status()
    return r.json()


def query_pipeline_rca(window_start: int, window_end: int) -> dict:
    r = requests.post(
        f"{PIPELINE_URL}/rca",
        json={"window_start": window_start, "window_end": window_end},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def build_inject_cmd(exp: dict) -> list[str]:
    """TODO #1 — dispatch fault_type to concrete subprocess command.

    Must cover all 10 fault types from §3:
        latency, network_loss, availability, cpu_saturation, memory,
        disk_fill, time_skew, network_partition, dns_latency, http_error

    Return a list suitable for subprocess.run(...).
    Example for latency:
        return ["pumba", "netem", "--duration", f"{dur}s",
                "delay", "--time", "500", exp["target"]]
    """
    raise NotImplementedError("Fill build_inject_cmd per §8.5")


def build_rollback_cmd(exp: dict) -> list[str]:
    """OPTIONAL helper — fault-specific rollback. Pumba auto-rolls on duration end.
    Toxiproxy needs explicit remove. tc/iptables need explicit cleanup.
    Return None if fault is self-clearing.
    """
    rb = exp.get("rollback", {}).get("method")
    if not rb:
        return None
    return rb.split()


def measure_during_window(exp: dict, t0: int) -> dict:
    duration = exp["blast_radius"]["duration_seconds"]
    capture = exp["measurement"]["capture_window_seconds"]
    t_end = t0 + capture
    alerts = query_pipeline_alerts(t0)
    rca = None
    detected_at = None
    for a in alerts:
        if a.get("fire_ts", 0) >= t0:
            detected_at = a["fire_ts"]
            break
    try:
        rca = query_pipeline_rca(t0, t_end)
    except Exception as e:
        rca = {"error": str(e)}
    mttd = (detected_at - t0) if detected_at else None
    return {
        "alerts": alerts,
        "rca": rca,
        "mttd_seconds": mttd,
        "detected": detected_at is not None,
    }


def score_one(exp: dict, observed: dict) -> dict:
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = (observed.get("rca") or {}).get("root_service")
    if gt_root.startswith("NOT "):
        rca_correct = rca_root is not None and rca_root != gt_root[4:]
    else:
        rca_correct = rca_root == gt_root
    return {
        "id": exp["id"],
        "name": exp["name"],
        "detected": observed["detected"],
        "mttd": observed["mttd_seconds"],
        "rca_service": rca_root,
        "rca_correct": rca_correct,
    }


def print_scoreboard(results: list[dict]) -> None:
    """TODO #2 — print confusion matrix per §8.6 format.

    Required output:
        ==== Chaos Run ====
        Total: <N>
        Detected: <N>/<total>
        RCA correct: <N>/<detected>
        False alarms in baseline windows: <N>     # 0 if no baseline interleaved
        Precision: <float>
        Recall: <float>
        MTTD p50: <s>, p95: <s>

        Per-experiment:
        | # | name              | detected | mttd  | rca_service  | rca_correct |
        |---|...

        Gaps identified:
        - <experiment id>: <symptom> → <suspected root cause in pipeline>
    """
    raise NotImplementedError("Fill print_scoreboard per §8.6")


def run_one(exp: dict) -> dict:
    print(f"[exp {exp['id']}] {exp['name']} — injecting fault...")
    t0 = int(time.time())
    cmd = build_inject_cmd(exp)
    subprocess.run(cmd, check=True, timeout=exp["blast_radius"]["duration_seconds"] + 30)
    observed = measure_during_window(exp, t0)
    rb = build_rollback_cmd(exp)
    if rb:
        subprocess.run(rb, check=False)
    print(f"[exp {exp['id']}] cooldown {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)
    return {**score_one(exp, observed), "observed_at_ts": t0, "raw": observed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    args = ap.parse_args()

    experiments = load_experiments(args.experiments)
    results = [run_one(e) for e in experiments]

    args.out.write_text(json.dumps(results, indent=2, default=str))
    print_scoreboard(results)


if __name__ == "__main__":
    main()
