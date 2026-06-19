#!/usr/bin/env python3
"""query_pipeline.py — quick CLI to inspect AIOps pipeline endpoints."""
import argparse
import json
import sys
import requests

PIPELINE_URL = "http://localhost:8000"


def cmd_alerts(args) -> None:
    r = requests.get(f"{PIPELINE_URL}/alerts", params={"since": args.since}, timeout=10)
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


def cmd_correlate(args) -> None:
    r = requests.post(f"{PIPELINE_URL}/correlate", json={"window": args.window}, timeout=15)
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


def cmd_rca(args) -> None:
    r = requests.post(
        f"{PIPELINE_URL}/rca",
        json={"window_start": args.start, "window_end": args.end},
        timeout=30,
    )
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("alerts"); a.add_argument("--since", type=int, default=0); a.set_defaults(func=cmd_alerts)
    c = sub.add_parser("correlate"); c.add_argument("--window", type=int, default=300); c.set_defaults(func=cmd_correlate)
    r = sub.add_parser("rca"); r.add_argument("--start", type=int, required=True); r.add_argument("--end", type=int, required=True); r.set_defaults(func=cmd_rca)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print(f"pipeline error: {e}", file=sys.stderr)
        sys.exit(2)
