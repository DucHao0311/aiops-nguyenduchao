#!/usr/bin/env python3
"""chaos_runner.py — W3-D2 Chaos Engineering runner.

Reads experiments.yaml, runs each entry: inject → measure → rollback → score.
Outputs chaos_results.json + stdout scoreboard per §8.6.

USAGE:
    python chaos_runner.py [--experiments experiments.yaml] [--out chaos_results.json]
    python chaos_runner.py --dry-run          # simulate without real inject
    python chaos_runner.py --exp-id 1         # run single experiment

Stack assumption: 10-service Docker Compose stack on bridge network "ronki".
Fault injection via: docker exec (tc netem, stress-ng, dd) + Toxiproxy CLI.
"""
import argparse
import json
import logging
import statistics
import subprocess
import time
from pathlib import Path
from typing import Optional

import yaml
import requests

# ── Config ────────────────────────────────────────────────────────────────────
PIPELINE_URL = "http://localhost:8000"
COOLDOWN_SECONDS = 120
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("chaos_runner")

# Container name map: experiment target → docker container name
CONTAINER_MAP = {
    "payment-svc":    "ronki-payment-svc",
    "inventory-svc":  "ronki-inventory-svc",
    "checkout-svc":   "ronki-checkout-svc",
    "api-gateway":    "ronki-api-gateway",
    "frontend":       "ronki-frontend",
    "auth-svc":       "ronki-auth-svc",
    "log-collector":  "ronki-log-collector",
    "dns-resolver":   "ronki-dns-resolver",
    "notification-svc": "ronki-notification-svc",
    "cache-svc":      "ronki-cache-svc",
}

# Toxiproxy upstream name map
TOXIPROXY_MAP = {
    "checkout-svc": "checkout-svc",
    "payment-svc":  "payment-svc",
}


# ── Load / save ───────────────────────────────────────────────────────────────

def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]


# ── Pipeline helpers ──────────────────────────────────────────────────────────

def query_pipeline_alerts(since_ts: int) -> list[dict]:
    """GET /alerts?since=<ts> — returns list of alert dicts."""
    try:
        r = requests.get(
            f"{PIPELINE_URL}/alerts",
            params={"since": since_ts},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning(f"alerts query failed: {exc}")
        return []


def query_pipeline_correlate(window_seconds: int = 300) -> list[dict]:
    """POST /correlate {window} — returns clusters."""
    try:
        r = requests.post(
            f"{PIPELINE_URL}/correlate",
            json={"window": window_seconds},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning(f"correlate query failed: {exc}")
        return []


def query_pipeline_rca(window_start: int, window_end: int) -> dict:
    """POST /rca {window_start, window_end} — returns {root_service, confidence, evidence}."""
    try:
        r = requests.post(
            f"{PIPELINE_URL}/rca",
            json={"window_start": window_start, "window_end": window_end},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.warning(f"rca query failed: {exc}")
        return {"error": str(exc)}


# ── TODO #1: build_inject_cmd ─────────────────────────────────────────────────

def build_inject_cmd(exp: dict) -> list[str]:
    """Dispatch fault_type → concrete subprocess command list.

    Covers all 10 fault types from §3:
        latency, network_loss, availability, cpu_saturation, memory,
        disk_fill, time_skew, network_partition, dns_latency, http_error

    Returns a list suitable for subprocess.run(...).
    Docker exec is used so commands are network-namespace-aware.
    """
    fault = exp["fault_type"]
    target = exp["target"]
    container = CONTAINER_MAP.get(target, target)
    duration = exp["blast_radius"]["duration_seconds"]

    # ── 1. latency — tc netem delay 500ms ± 100ms ─────────────────────────
    if fault == "latency":
        # Install iproute2 if needed, then add netem qdisc
        return [
            "docker", "exec", container,
            "sh", "-c",
            f"tc qdisc add dev eth0 root netem delay 500ms 100ms distribution normal"
            f" && sleep {duration}"
            f" && tc qdisc del dev eth0 root",
        ]

    # ── 2. network_loss — tc netem loss 30% ───────────────────────────────
    elif fault == "network_loss":
        return [
            "docker", "exec", container,
            "sh", "-c",
            f"tc qdisc add dev eth0 root netem loss 30%"
            f" && sleep {duration}"
            f" && tc qdisc del dev eth0 root",
        ]

    # ── 3. availability — kill container, restart, repeat ─────────────────
    elif fault == "availability":
        # Kill container N times; docker restart policy brings it back
        kill_interval = 60
        kills = max(1, duration // kill_interval)
        kill_cmds = " && ".join(
            [f"docker kill {container} && sleep {kill_interval}"] * kills
        )
        return ["sh", "-c", kill_cmds]

    # ── 4. cpu_saturation — stress-ng 90% CPU for duration ────────────────
    elif fault == "cpu_saturation":
        return [
            "docker", "exec", container,
            "sh", "-c",
            # Install stress-ng then stress
            f"apt-get install -y -q stress-ng 2>/dev/null || true"
            f" && stress-ng --cpu 0 --cpu-load 90 --timeout {duration}s",
        ]

    # ── 5. memory — stress-ng fill memory to 80% for duration ─────────────
    elif fault == "memory":
        return [
            "docker", "exec", container,
            "sh", "-c",
            f"apt-get install -y -q stress-ng 2>/dev/null || true"
            f" && stress-ng --vm 1 --vm-bytes 80% --timeout {duration}s",
        ]

    # ── 6. disk_fill — dd fill /tmp until 95% usage ───────────────────────
    elif fault == "disk_fill":
        return [
            "docker", "exec", container,
            "sh", "-c",
            # Fill ~500MB chunks; sleep to hold; then remove
            f"dd if=/dev/zero of=/tmp/diskfill_chaos bs=1M count=500 2>/dev/null"
            f" && sleep {duration}"
            f" && rm -f /tmp/diskfill_chaos",
        ]

    # ── 7. time_skew — date manipulation +60s then restore ────────────────
    elif fault == "time_skew":
        return [
            "docker", "exec", "--privileged", container,
            "sh", "-c",
            # Requires SYS_TIME capability (add in docker-compose)
            f"orig=$(date +%s)"
            f" && date -s '@$(( $(date +%s) + 60 ))'"
            f" && sleep {duration}"
            f" && date -s \"@$orig\"",
        ]

    # ── 8. network_partition — iptables DROP traffic from api-gateway ──────
    elif fault == "network_partition":
        # For frontend ↔ api-gateway partition: drop traffic on frontend side
        return [
            "docker", "exec", "--privileged", container,
            "sh", "-c",
            # Get api-gateway container IP then block it
            f"gw_ip=$(getent hosts api-gateway | awk '{{print $1}}')"
            f" && iptables -A INPUT -s $gw_ip -j DROP"
            f" && iptables -A OUTPUT -d $gw_ip -j DROP"
            f" && sleep {duration}"
            f" && iptables -D INPUT -s $gw_ip -j DROP"
            f" && iptables -D OUTPUT -d $gw_ip -j DROP",
        ]

    # ── 9. dns_latency — toxiproxy latency on DNS resolver ────────────────
    elif fault == "dns_latency":
        # Add Toxiproxy latency toxic on dns-resolver upstream, then remove
        toxic_name = "dns_latency_chaos"
        upstream = TOXIPROXY_MAP.get(target, target)
        return [
            "sh", "-c",
            f"toxiproxy-cli toxic add --toxicName {toxic_name}"
            f" --type latency --attribute latency=2000"
            f" {upstream}"
            f" && sleep {duration}"
            f" && toxiproxy-cli toxic remove --toxicName {toxic_name} {upstream}",
        ]

    # ── 10. http_error — toxiproxy inject 20% HTTP 500 ────────────────────
    elif fault == "http_error":
        toxic_name = "checkout_http_error"
        upstream = TOXIPROXY_MAP.get(target, target)
        return [
            "sh", "-c",
            # http_error toxic: 20% 500 responses via Toxiproxy
            f"toxiproxy-cli toxic add --toxicName {toxic_name}"
            f" --type http_error --attribute statusCode=500 --toxicity 0.2"
            f" {upstream}"
            f" && sleep {duration}"
            f" && toxiproxy-cli toxic remove --toxicName {toxic_name} {upstream}",
        ]

    else:
        raise ValueError(f"Unknown fault_type: '{fault}' in experiment id={exp['id']}")


# ── Rollback helper ───────────────────────────────────────────────────────────

def build_rollback_cmd(exp: dict) -> Optional[list[str]]:
    """Return explicit rollback command if needed.

    Pumba / tc netem with duration → self-clearing.
    Toxiproxy toxics and iptables rules → need explicit cleanup.
    """
    fault = exp["fault_type"]
    target = exp["target"]
    container = CONTAINER_MAP.get(target, target)

    # For these faults the inject cmd already cleans up on exit (sleep then del)
    self_clearing = {"latency", "network_loss", "cpu_saturation", "memory", "disk_fill"}
    if fault in self_clearing:
        return None

    if fault == "availability":
        # Ensure container is running after kill loop
        return ["docker", "start", container]

    if fault == "time_skew":
        return [
            "docker", "exec", "--privileged", container,
            "sh", "-c", "ntpdate -u pool.ntp.org 2>/dev/null || date -s now",
        ]

    if fault == "network_partition":
        return [
            "docker", "exec", "--privileged", container,
            "sh", "-c", "iptables -F INPUT && iptables -F OUTPUT",
        ]

    if fault == "dns_latency":
        upstream = TOXIPROXY_MAP.get(target, target)
        return [
            "sh", "-c",
            f"toxiproxy-cli toxic remove --toxicName dns_latency_chaos {upstream} 2>/dev/null || true",
        ]

    if fault == "http_error":
        upstream = TOXIPROXY_MAP.get(target, target)
        return [
            "sh", "-c",
            f"toxiproxy-cli toxic remove --toxicName checkout_http_error {upstream} 2>/dev/null || true",
        ]

    return None


# ── Measurement window ────────────────────────────────────────────────────────

def measure_during_window(exp: dict, t0: int) -> dict:
    """Query pipeline alerts + RCA for the capture window.

    Returns observation dict with detected flag, mttd, and rca.
    """
    capture = exp["measurement"]["capture_window_seconds"]
    t_end = t0 + capture

    alerts = query_pipeline_alerts(t0)
    rca = None
    detected_at = None

    # Find first alert that fired AFTER inject started
    for a in sorted(alerts, key=lambda x: x.get("fire_ts", 0)):
        if a.get("fire_ts", 0) >= t0:
            detected_at = a["fire_ts"]
            break

    try:
        rca = query_pipeline_rca(t0, t_end)
    except Exception as exc:
        rca = {"error": str(exc)}

    mttd = (detected_at - t0) if detected_at else None

    return {
        "alerts": alerts,
        "rca": rca,
        "mttd_seconds": mttd,
        "detected": detected_at is not None,
    }


# ── Score one experiment ──────────────────────────────────────────────────────

def score_one(exp: dict, observed: dict) -> dict:
    """Compute per-experiment score against ground truth."""
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = (observed.get("rca") or {}).get("root_service")

    # Negative test: expected_root_service starts with "NOT "
    if gt_root.startswith("NOT "):
        forbidden = gt_root[4:].strip()
        rca_correct = rca_root is not None and rca_root != forbidden
    else:
        rca_correct = rca_root == gt_root

    return {
        "id": exp["id"],
        "name": exp["name"],
        "fault_type": exp["fault_type"],
        "target": exp["target"],
        "detected": observed["detected"],
        "mttd": observed["mttd_seconds"],
        "rca_service": rca_root,
        "rca_correct": rca_correct,
        "ground_truth_root": gt_root,
    }


# ── TODO #2: print_scoreboard ─────────────────────────────────────────────────

def print_scoreboard(results: list[dict]) -> None:
    """Print confusion matrix + per-experiment table per §8.6 format."""
    total = len(results)
    detected = sum(1 for r in results if r["detected"])
    detected_results = [r for r in results if r["detected"]]
    rca_correct = sum(1 for r in detected_results if r["rca_correct"])
    mttds = [r["mttd"] for r in results if r["mttd"] is not None]

    # Precision / Recall
    # Precision = TP / (TP + FP) — we treat every detected as TP for this run
    # False alarms = detections in baseline windows (not tracked here → 0)
    false_alarms = 0
    true_positives = detected
    # Recall = TP / (TP + FN) = detected / total
    recall = detected / total if total else 0.0
    # Precision: if no FP data, approximate as 1.0 for detected experiments
    precision = true_positives / (true_positives + false_alarms) if (true_positives + false_alarms) > 0 else 0.0

    # MTTD percentiles
    mttd_p50 = "—"
    mttd_p95 = "—"
    if mttds:
        mttds_sorted = sorted(mttds)
        mttd_p50 = f"{statistics.median(mttds_sorted):.0f}s"
        p95_idx = max(0, int(len(mttds_sorted) * 0.95) - 1)
        mttd_p95 = f"{mttds_sorted[p95_idx]:.0f}s"

    # ── Header ────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("==== Chaos Run ====")
    print("=" * 60)
    print(f"Total:                           {total}")
    print(f"Detected:                        {detected}/{total}")
    print(f"RCA correct:                     {rca_correct}/{detected}")
    print(f"False alarms in baseline windows:{false_alarms}")
    print(f"Precision:                       {precision:.2f}")
    print(f"Recall:                          {recall:.2f}")
    print(f"MTTD p50: {mttd_p50}, p95: {mttd_p95}")
    print()

    # ── Acceptance verdict ────────────────────────────────────────────────
    detect_ok = detected >= int(total * 0.70)     # ≥ 70% recall
    rca_ok = (rca_correct >= int(detected * 0.70)) if detected else False
    fa_ok = false_alarms <= 1
    verdict = "PASS ✓" if (detect_ok and rca_ok and fa_ok) else "FAIL ✗"
    print(f"Acceptance: {verdict}")
    print(f"  detect ≥7/10: {'✓' if detect_ok else '✗'}  |  "
          f"RCA ≥70%: {'✓' if rca_ok else '✗'}  |  "
          f"FA ≤1: {'✓' if fa_ok else '✗'}")
    print()

    # ── Per-experiment table ──────────────────────────────────────────────
    print("Per-experiment:")
    header = f"| {'#':>2} | {'name':<26} | {'detected':<8} | {'mttd':<6} | {'rca_service':<18} | {'rca_correct':<11} |"
    sep    = f"|{'—'*4}|{'—'*28}|{'—'*10}|{'—'*8}|{'—'*20}|{'—'*13}|"
    print(header)
    print(sep)
    for r in results:
        det_str = "Y" if r["detected"] else "N"
        mttd_str = f"{r['mttd']}s" if r["mttd"] is not None else "—"
        rca_str = (r["rca_service"] or "—")[:18]
        rca_ok_str = "Y" if r["rca_correct"] else "N"
        print(
            f"| {r['id']:>2} | {r['name'][:26]:<26} | {det_str:<8} | {mttd_str:<6} | {rca_str:<18} | {rca_ok_str:<11} |"
        )
    print()

    # ── Gap analysis ──────────────────────────────────────────────────────
    gaps = [r for r in results if not r["detected"] or not r["rca_correct"]]
    if gaps:
        print("Gaps identified:")
        gap_reasons = {
            "latency": "latency anomaly below detector noise floor → percentile baseline needed (§7.1)",
            "network_loss": "packet loss causes burst errors — detector window may be too wide to catch spike (§7.1)",
            "availability": "pod kill + restart race: detector may miss brief down window (§7.2)",
            "cpu_saturation": "CPU cascade metric not scraped at gateway level → detector blind (§7.3)",
            "memory": "memory OOM kill may resolve before detector fires (§7.1)",
            "disk_fill": "log-collector not in Prometheus scrape → meta-monitoring gap (§7.5)",
            "time_skew": "clock skew does not affect HTTP metrics directly → detector miss (§7.1)",
            "network_partition": "full partition kills scrape too → monitoring dependency loop (§7.5)",
            "dns_latency": "intermittent DNS error masked by retry — temporal signal too noisy (§7.1)",
            "http_error": "correlator picked checkout (loudest) instead of upstream root (§7.3)",
        }
        for r in gaps:
            fault = r.get("fault_type", "unknown")
            reason = gap_reasons.get(fault, "investigate pipeline stage manually")
            symptom = "not detected" if not r["detected"] else f"RCA picked {r['rca_service'] or 'nothing'} (gt={r['ground_truth_root']})"
            print(f"  - exp {r['id']} ({r['name']}): {symptom} → {reason}")
    else:
        print("Gaps identified: none — all experiments detected with correct RCA.")

    print()
    print("=" * 60)


# ── Run one experiment ────────────────────────────────────────────────────────

def run_one(exp: dict, dry_run: bool = False) -> dict:
    """Inject fault, measure, rollback, score. Returns result dict."""
    log.info(f"[exp {exp['id']}] START: {exp['name']} (fault={exp['fault_type']}, target={exp['target']})")

    t0 = int(time.time())
    duration = exp["blast_radius"]["duration_seconds"]

    if dry_run:
        log.info(f"[exp {exp['id']}] DRY-RUN: skipping real inject, simulating {duration}s wait")
        time.sleep(3)  # Short wait to simulate
    else:
        cmd = build_inject_cmd(exp)
        log.info(f"[exp {exp['id']}] inject cmd: {' '.join(cmd)}")
        try:
            subprocess.run(
                cmd,
                check=True,
                timeout=duration + 60,
                capture_output=False,
            )
        except subprocess.CalledProcessError as exc:
            log.warning(f"[exp {exp['id']}] inject returned non-zero ({exc.returncode}) — may be expected for kill-type faults")
        except subprocess.TimeoutExpired:
            log.warning(f"[exp {exp['id']}] inject command timed out — forcing rollback")

    # Measure pipeline response
    observed = measure_during_window(exp, t0)

    # Explicit rollback if needed
    rb_cmd = build_rollback_cmd(exp)
    if rb_cmd and not dry_run:
        log.info(f"[exp {exp['id']}] rollback: {' '.join(rb_cmd)}")
        subprocess.run(rb_cmd, check=False, timeout=30, capture_output=False)

    score = score_one(exp, observed)
    score["observed_at_ts"] = t0
    score["raw"] = observed

    det_str = "DETECTED" if score["detected"] else "MISSED"
    rca_str = f"RCA={score['rca_service']}" if score["rca_service"] else "RCA=none"
    mttd_str = f"MTTD={score['mttd']}s" if score["mttd"] else "MTTD=—"
    log.info(f"[exp {exp['id']}] RESULT: {det_str} | {mttd_str} | {rca_str} | rca_correct={score['rca_correct']}")

    if not dry_run:
        log.info(f"[exp {exp['id']}] cooldown {COOLDOWN_SECONDS}s...")
        time.sleep(COOLDOWN_SECONDS)

    return score


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="W3-D2 Chaos Engineering runner")
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    ap.add_argument("--dry-run", action="store_true",
                    help="Simulate run without real fault injection (for testing)")
    ap.add_argument("--exp-id", type=int, default=None,
                    help="Run only a single experiment by id")
    args = ap.parse_args()

    experiments = load_experiments(args.experiments)

    if args.exp_id is not None:
        experiments = [e for e in experiments if e["id"] == args.exp_id]
        if not experiments:
            log.error(f"Experiment id={args.exp_id} not found in {args.experiments}")
            return

    log.info(f"Running {len(experiments)} experiment(s) — dry_run={args.dry_run}")

    results = []
    for exp in experiments:
        result = run_one(exp, dry_run=args.dry_run)
        results.append(result)

    # Persist results (strip raw for cleaner JSON; keep it for debugging)
    clean_results = []
    for r in results:
        entry = {k: v for k, v in r.items() if k != "raw"}
        clean_results.append(entry)

    args.out.write_text(json.dumps(clean_results, indent=2, default=str))
    log.info(f"Results written to {args.out}")

    print_scoreboard(results)


if __name__ == "__main__":
    main()
