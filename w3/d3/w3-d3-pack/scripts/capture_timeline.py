#!/usr/bin/env python3
"""capture_timeline.py — capture events into timeline.json with UTC timestamps.

Sources (best-effort, each one optional):
  1. docker compose events (container lifecycle)
  2. Prometheus /api/v1/alerts (active alerts)
  3. AIOps pipeline /alerts (if running)

Output schema:
    [{"ts": "<iso8601 UTC>", "source": "<docker|prom|pipeline>", "event": "<str>"}, ...]
"""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone

import requests

PROM_URL = "http://localhost:9090"
PIPELINE_URL = "http://localhost:8000"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def docker_events_tail(duration: int) -> list[dict]:
    try:
        out = subprocess.run(
            ["docker", "events", "--since", f"{duration}s", "--until", "0s",
             "--format", "{{.Time}}\t{{.Type}}\t{{.Status}}\t{{.Actor.Attributes.name}}"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        events = []
        for line in out.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            ts_epoch = int(parts[0])
            events.append({
                "ts": datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat(timespec="seconds"),
                "source": "docker",
                "event": f"{parts[2]} {parts[3]}",
            })
        return events
    except Exception:
        return []


def prom_alerts() -> list[dict]:
    try:
        r = requests.get(f"{PROM_URL}/api/v1/alerts", timeout=3)
        r.raise_for_status()
        events = []
        for a in r.json().get("data", {}).get("alerts", []):
            events.append({
                "ts": a.get("activeAt") or now_iso(),
                "source": "prom",
                "event": f"alert={a.get('labels', {}).get('alertname')} state={a.get('state')}",
            })
        return events
    except Exception:
        return []


def pipeline_alerts(since: int) -> list[dict]:
    try:
        r = requests.get(f"{PIPELINE_URL}/alerts", params={"since": since}, timeout=3)
        r.raise_for_status()
        events = []
        for a in r.json():
            events.append({
                "ts": datetime.fromtimestamp(a.get("fire_ts", 0), tz=timezone.utc).isoformat(timespec="seconds"),
                "source": "pipeline",
                "event": f"pipeline-alert={a.get('name')} svc={a.get('service')}",
            })
        return events
    except Exception:
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=600)
    ap.add_argument("--out", default="timeline.json")
    args = ap.parse_args()

    start_ts = int(time.time())
    print(f"Capturing timeline for {args.duration}s...")
    time.sleep(args.duration)

    events = []
    events += docker_events_tail(args.duration)
    events += prom_alerts()
    events += pipeline_alerts(start_ts)
    events.sort(key=lambda e: e["ts"])

    with open(args.out, "w") as f:
        json.dump(events, f, indent=2)
    print(f"Wrote {args.out} — {len(events)} events")


if __name__ == "__main__":
    main()
