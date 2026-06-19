#!/usr/bin/env python3
"""score_run.py — read chaos_results.json + probe.log, emit scoreboard."""
import argparse
import json
import statistics
from pathlib import Path


def parse_probe(path: Path) -> dict:
    if not path.exists():
        return {"total": 0, "pass": 0, "pass_rate": None}
    total = 0
    passed = 0
    for line in path.open():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            total += 1
            if parts[1] == "pass":
                passed += 1
    return {
        "total": total,
        "pass": passed,
        "pass_rate": (passed / total) if total else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="chaos_results.json", type=Path)
    ap.add_argument("--probe", default="probe.log", type=Path)
    args = ap.parse_args()

    results = json.loads(args.results.read_text())
    probe = parse_probe(args.probe)

    total = len(results)
    detected = sum(1 for r in results if r["detected"])
    rca_correct = sum(1 for r in results if r["rca_correct"])
    mttds = [r["mttd"] for r in results if r["mttd"] is not None]

    print("==== Chaos Run ====")
    print(f"Total: {total}")
    print(f"Detected: {detected}/{total}")
    print(f"RCA correct: {rca_correct}/{detected}" if detected else "RCA correct: 0/0")
    if mttds:
        p50 = statistics.median(mttds)
        p95 = sorted(mttds)[max(0, int(len(mttds) * 0.95) - 1)]
        print(f"MTTD p50: {p50}s, p95: {p95}s")
    print(f"External probe pass-rate: {probe['pass_rate']:.2%}" if probe['pass_rate'] is not None else "External probe pass-rate: n/a")
    print()
    print("Per-experiment:")
    print(f"| {'#':>2} | {'name':<25} | {'detected':<8} | {'mttd':<6} | {'rca_service':<15} | {'rca_correct':<11} |")
    print("|----|" + "-" * 27 + "|" + "-" * 10 + "|" + "-" * 8 + "|" + "-" * 17 + "|" + "-" * 13 + "|")
    for r in results:
        print(f"| {r['id']:>2} | {r['name'][:25]:<25} | {'Y' if r['detected'] else 'N':<8} | {str(r['mttd'] or '—'):<6} | {str(r['rca_service'] or '—')[:15]:<15} | {'Y' if r['rca_correct'] else 'N':<11} |")


if __name__ == "__main__":
    main()
