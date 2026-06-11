#!/usr/bin/env python3
"""Simple log pattern clusterer — Drain3-style fixed-depth template extraction.

Without external Drain3 dependency: implements the core Drain algorithm in ~80 lines.
Group log lines by length, then descend a fixed-depth tree using token similarity.

Usage:
    from models.log_clusterer import LogClusterer
    lc = LogClusterer.train_from_db()
    template = lc.match("Connection refused to 10.42.1.7:8080")
"""
from __future__ import annotations
import argparse, sqlite3, re, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "workshop.db"


TOKEN_SPLIT = re.compile(r"[\s\(\)\[\]\{\}=,:]+")
# Patterns to mask as wildcards
NUMERIC = re.compile(r"^\d+(\.\d+)?(ms|s|MB|GB)?$")
IP_PORT = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(:\d+)?$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}", re.I)
HEX = re.compile(r"^[0-9a-f]{8,}$", re.I)


def tokenize(line: str) -> list[str]:
    return [t for t in TOKEN_SPLIT.split(line.strip()) if t]


def mask_token(tok: str) -> str:
    if NUMERIC.match(tok): return "<NUM>"
    if IP_PORT.match(tok): return "<IP>"
    if UUID.match(tok): return "<UUID>"
    if HEX.match(tok): return "<HEX>"
    return tok


def template_from_tokens(toks: list[str]) -> str:
    return " ".join(mask_token(t) for t in toks)


class LogClusterer:
    def __init__(self):
        # cluster_id -> {"template": str, "count": int, "examples": list}
        self.clusters: dict[int, dict] = {}
        # length -> list[cluster_id]
        self.by_length: dict[int, list[int]] = {}

    def add_line(self, line: str, count_increment: int = 1) -> int:
        toks = tokenize(line)
        n = len(toks)
        candidates = self.by_length.get(n, [])
        # find best matching cluster: count matching positions / total
        best_cid, best_score = None, 0.0
        for cid in candidates:
            ct = self.clusters[cid]["template"].split()
            if len(ct) != n: continue
            matches = sum(1 for a, b in zip(ct, [mask_token(t) for t in toks]) if a == b or a.startswith("<"))
            score = matches / n
            if score > best_score:
                best_score, best_cid = score, cid
        if best_score >= 0.65:
            self.clusters[best_cid]["count"] += count_increment
            return best_cid
        # new cluster
        cid = len(self.clusters)
        self.clusters[cid] = {"template": template_from_tokens(toks), "count": count_increment,
                              "examples": [line[:200]]}
        self.by_length.setdefault(n, []).append(cid)
        return cid

    def match(self, line: str) -> dict:
        cid = self.add_line(line, count_increment=0)
        return {"cluster_id": cid, **self.clusters[cid]}

    @classmethod
    def train_from_db(cls) -> "LogClusterer":
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT service, count, pattern FROM log_patterns").fetchall()
        # Also pull from raw coroot logs
        try:
            raw_logs = json.loads((ROOT / "data" / "raw" / "05_logs.json").read_text())
            for app_id, body in raw_logs.items():
                for p in body.get("patterns", []):
                    sample = p.get("sample", "")
                    # extract just message after timestamp/level
                    msg_match = re.search(r'"message":\s*"([^"]+)"', sample)
                    msg = msg_match.group(1) if msg_match else sample[:200]
                    rows.append((app_id.split(":")[-1], p.get("count", 1), msg))
        except Exception:
            pass
        conn.close()
        lc = cls()
        for svc, count, pattern in rows:
            lc.add_line(pattern, count_increment=count)
        return lc

    def top(self, n: int = 20) -> list[dict]:
        return sorted(({"cluster_id": cid, **c} for cid, c in self.clusters.items()),
                      key=lambda x: -x["count"])[:n]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--show", type=int, default=15)
    p.add_argument("--match", help="match a log line to existing cluster")
    args = p.parse_args()
    lc = LogClusterer.train_from_db()
    if args.match:
        print(json.dumps(lc.match(args.match), indent=2))
        return
    print(f"Trained {len(lc.clusters)} log templates from real + scenario log patterns.")
    print(f"\nTop {args.show} by count:")
    for c in lc.top(args.show):
        ex = (c["examples"][0] if c["examples"] else "")[:100]
        print(f"  [{c['count']:5d}x] {c['template'][:100]}")
        print(f"           ex: {ex}")


if __name__ == "__main__":
    main()
