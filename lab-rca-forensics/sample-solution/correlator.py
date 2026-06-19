#!/usr/bin/env python3
"""Alert correlator for the RCA Forensics lab.

Grouping heuristic
------------------
Two alerts belong to the same chain if BOTH conditions hold:

  1. They fire within a 90-minute window of any other alert already in
     the chain. The window is wide enough to absorb slow-burn incidents
     (memory leaks, drift) while staying narrow enough not to merge
     unrelated daily incidents on a multi-day timeline.
  2. There is a directed dependency path between their `service` fields
     in `topology.json` (either direction is acceptable; what matters is
     that they are connected in the call graph).

Root selection
--------------
The "root" of a chain is the service whose log file shows the earliest
WARN or ERROR event. The alert chosen as the chain's root alert is the
earliest-firing alert whose `service` field equals that originating
service. The originating log line is included in the output, since it
often points to the true upstream cause (e.g., a third-party endpoint
that has no alert of its own).

The script writes to stdout. It takes one positional argument: the path
to the data pack directory.

Usage
-----
    python correlator.py ../data-pack/
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

WINDOW = timedelta(minutes=90)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load(data_pack: Path):
    topology = json.loads((data_pack / "topology.json").read_text())
    alerts = json.loads((data_pack / "alerts.json").read_text())
    for a in alerts:
        a["fired_at_dt"] = parse_ts(a["fired_at"])
    return topology, alerts


def build_dep_graph(topology: dict) -> dict[str, set[str]]:
    forward: dict[str, set[str]] = defaultdict(set)
    for svc in topology["services"]:
        forward[svc["name"]] = set(svc["deps"])
    return forward


def reaches(forward: dict[str, set[str]], src: str, dst: str) -> bool:
    if src == dst:
        return True
    visited = {src}
    queue = deque([src])
    while queue:
        node = queue.popleft()
        for nxt in forward.get(node, ()):
            if nxt == dst:
                return True
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return False


def linked(forward: dict[str, set[str]], a: str, b: str) -> bool:
    return reaches(forward, a, b) or reaches(forward, b, a)


def cluster(alerts: list[dict],
            forward: dict[str, set[str]]) -> list[list[dict]]:
    alerts = sorted(alerts, key=lambda a: a["fired_at_dt"])
    chains: list[list[dict]] = []
    for a in alerts:
        placed = False
        for chain in chains:
            in_window = any(
                abs((a["fired_at_dt"] - x["fired_at_dt"]).total_seconds())
                <= WINDOW.total_seconds()
                for x in chain
            )
            connected = any(linked(forward, a["service"], x["service"])
                            for x in chain)
            if in_window and connected:
                chain.append(a)
                placed = True
                break
        if not placed:
            chains.append([a])
    return chains


def first_warn_or_error(logs_dir: Path, svc: str,
                        t_low: datetime, t_high: datetime):
    """Earliest WARN/ERROR for this service within [t_low, t_high]."""
    path = logs_dir / f"{svc}.jsonl"
    if not path.exists():
        return None
    earliest_ts = None
    earliest_rec = None
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("level") not in ("WARN", "ERROR"):
                continue
            ts = parse_ts(rec["ts"])
            if ts < t_low or ts > t_high:
                continue
            if earliest_ts is None or ts < earliest_ts:
                earliest_ts = ts
                earliest_rec = rec
    if earliest_ts is None:
        return None
    return earliest_ts, earliest_rec


def pick_originator(chain: list[dict], logs_dir: Path):
    # Constrain log search to a window anchored on the chain's first alert.
    # 30 minutes before the earliest alert is plenty for upstream signals
    # to appear without bleeding into adjacent incidents.
    chain_t_min = min(a["fired_at_dt"] for a in chain)
    chain_t_max = max(a["fired_at_dt"] for a in chain)
    t_low = chain_t_min - timedelta(minutes=30)
    t_high = chain_t_max + timedelta(minutes=5)
    services = sorted({a["service"] for a in chain})
    best = None
    for svc in services:
        result = first_warn_or_error(logs_dir, svc, t_low, t_high)
        if result is None:
            continue
        ts, rec = result
        if best is None or ts < best[0]:
            best = (ts, svc, rec)
    return best


def pick_root_alert(chain: list[dict], originator_svc: str) -> dict:
    candidates = [a for a in chain if a["service"] == originator_svc]
    if not candidates:
        candidates = list(chain)
    candidates.sort(key=lambda a: a["fired_at_dt"])
    return candidates[0]


def render(chains: list[list[dict]], logs_dir: Path) -> None:
    rendered = []
    for ch in chains:
        origin = pick_originator(ch, logs_dir)
        if origin is None:
            origin_svc = ch[0]["service"]
            origin_rec = None
            origin_ts = ch[0]["fired_at_dt"]
        else:
            origin_ts, origin_svc, origin_rec = origin
        root = pick_root_alert(ch, origin_svc)
        rendered.append((root, ch, origin_svc, origin_ts, origin_rec))

    rendered.sort(key=lambda r: r[0]["fired_at_dt"])

    for idx, (root, chain, origin_svc, origin_ts, origin_rec) in enumerate(
            rendered, start=1):
        print(f"CHAIN {idx}: {root['name']} ({root['service']}) "
              f"@ {root['fired_at']}")
        if origin_rec is not None:
            ts_str = origin_ts.isoformat().replace("+00:00", "Z")
            extras = origin_rec.get("extra") or {}
            upstream = extras.get("endpoint") or extras.get("target") or "-"
            print(f"  originator: {origin_svc}.{origin_rec.get('component','?')}"
                  f" @ {ts_str}")
            print(f"    first trouble: {origin_rec.get('level')} "
                  f"{origin_rec.get('msg')} "
                  f"(upstream: {upstream})")
        children = [a for a in chain if a["id"] != root["id"]]
        children.sort(key=lambda a: a["fired_at_dt"])
        if children:
            print("  child alerts:")
            for c in children:
                delta = int(
                    (c["fired_at_dt"] - root["fired_at_dt"]).total_seconds())
                sign = "+" if delta >= 0 else ""
                print(f"    ├─ {c['name']} ({c['service']}) "
                      f"({sign}{delta}s)")
        print()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: correlator.py <data-pack-dir>", file=sys.stderr)
        return 2
    data_pack = Path(argv[1]).resolve()
    if not data_pack.is_dir():
        print(f"not a directory: {data_pack}", file=sys.stderr)
        return 2
    topology, alerts = load(data_pack)
    forward = build_dep_graph(topology)
    chains = cluster(alerts, forward)
    render(chains, data_pack / "logs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
