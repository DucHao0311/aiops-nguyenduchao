#!/usr/bin/env python3
"""IsolationForest anomaly detector trained on baseline (steady-state) metrics.

Usage as library:
    from models.anomaly_detector import AnomalyDetector
    det = AnomalyDetector.train_from_baseline()
    score = det.score(service="esb", metric="latency_p99_ms", value=2200)
    print(score)  # negative = anomaly, positive = normal

Usage as CLI (sanity):
    uv run python models/anomaly-detector.py --scenario S05 --service esb --metric latency_p99_ms
"""
from __future__ import annotations
import argparse, json, sqlite3, pickle
from pathlib import Path
import numpy as np
from sklearn.ensemble import IsolationForest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "workshop.db"
MODEL_PATH = ROOT / "models" / "anomaly-detector.pkl"


class AnomalyDetector:
    def __init__(self, models: dict, baseline_stats: dict):
        self.models = models  # {(svc, metric): IsolationForest}
        self.stats = baseline_stats  # {(svc, metric): {"mean": x, "std": y}}

    @classmethod
    def train_from_baseline(cls) -> "AnomalyDetector":
        """Train one IsolationForest per (service, metric) using the first 60 min of each scenario.

        Per scenario, the first 60 samples are baseline; subsequent samples are scenario phases.
        We use the union of all-scenario baselines per (svc, metric) as training data.
        """
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # First 60 samples per scenario = baseline window
        rows = conn.execute("""
            SELECT service, metric, value, scenario, timestamp
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY scenario, service, metric ORDER BY timestamp) AS rn
                FROM metrics
            ) WHERE rn <= 60
        """).fetchall()
        conn.close()
        buckets: dict[tuple[str, str], list[float]] = {}
        for r in rows:
            buckets.setdefault((r["service"], r["metric"]), []).append(r["value"])
        models, stats = {}, {}
        for key, vals in buckets.items():
            arr = np.array(vals).reshape(-1, 1)
            if len(arr) < 20 or np.std(arr) < 1e-9:
                # too few samples or zero variance — skip IF model, just store stats
                stats[key] = {"mean": float(np.mean(arr)), "std": float(np.std(arr) + 1e-9), "n": len(arr)}
                continue
            m = IsolationForest(contamination=0.02, n_estimators=80, random_state=42)
            m.fit(arr)
            models[key] = m
            stats[key] = {"mean": float(np.mean(arr)), "std": float(np.std(arr)), "n": len(arr)}
        return cls(models, stats)

    def score(self, service: str, metric: str, value: float) -> dict:
        """Return both IsolationForest anomaly score AND 3-sigma deviation."""
        key = (service, metric)
        out = {"service": service, "metric": metric, "value": value}
        st = self.stats.get(key)
        if st:
            out["baseline_mean"] = st["mean"]
            out["baseline_std"] = st["std"]
            out["z_score"] = (value - st["mean"]) / max(1e-9, st["std"])
            out["three_sigma_anomaly"] = abs(out["z_score"]) > 3
        if key in self.models:
            m = self.models[key]
            score = float(m.decision_function(np.array([[value]]))[0])
            out["if_score"] = score
            out["if_anomaly"] = score < 0
        return out

    def save(self, path: Path = MODEL_PATH):
        with open(path, "wb") as f:
            pickle.dump({"models": self.models, "stats": self.stats}, f)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "AnomalyDetector":
        with open(path, "rb") as f:
            d = pickle.load(f)
        return cls(d["models"], d["stats"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--train", action="store_true", help="train + save")
    p.add_argument("--scenario", default="S05")
    p.add_argument("--service", default="esb")
    p.add_argument("--metric", default="latency_p99_ms")
    args = p.parse_args()

    if args.train or not MODEL_PATH.exists():
        print(f"Training from baseline...")
        det = AnomalyDetector.train_from_baseline()
        det.save()
        print(f"Saved -> {MODEL_PATH} ({len(det.models)} IF models, {len(det.stats)} stats)")
    else:
        det = AnomalyDetector.load()

    # Score all samples for the chosen (scenario, service, metric)
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""SELECT timestamp, value FROM metrics
                           WHERE scenario = ? AND service = ? AND metric = ? ORDER BY timestamp""",
                        (args.scenario, args.service, args.metric)).fetchall()
    conn.close()
    print(f"\nAnomaly scoring {args.service}/{args.metric} in {args.scenario}")
    anomaly_count = 0
    first_anom_ts = None
    for ts, v in rows:
        s = det.score(args.service, args.metric, v)
        if s.get("if_anomaly") or s.get("three_sigma_anomaly"):
            anomaly_count += 1
            if first_anom_ts is None: first_anom_ts = ts
    print(f"  Anomalies: {anomaly_count}/{len(rows)} samples")
    print(f"  First anomaly at: {first_anom_ts}")


if __name__ == "__main__":
    main()
