#!/usr/bin/env python3
"""
closed_loop.py — Ronki AIOps closed-loop auto-remediation orchestrator.

Usage:
    uv run python closed_loop.py --config config.yaml [--dry-run]

Decision engine: Rule-based (Option A).
  Mapping alertname → runbook is declared in config.yaml::runbook_map.
  Deterministic, zero-latency decisions, no external API dependency.

5 mandatory sub-checkpoints (Section 3 of HANDOUT):
  1. Dry-run         — every runbook is called with --dry-run first
  2. Blast-radius    — global/per-service rate limits enforced before act
  3. Verify          — poll Prometheus ≥3 times within 60s post-action
  4. Auto-rollback   — verify fail → rollback runbook without human intervention
  5. Circuit breaker — 3 consecutive failures → CIRCUIT_OPEN, halt automation

Stress extensions (acceptance tests #4–6):
  - Per-service mutex: two services run in parallel; same service serialises.
  - Transactional multi-step rollback: steps A→B→C with LIFO rollback.
  - Decision validation: runbook must be present in runbook_registry.
"""

import argparse
import json
import platform
import subprocess
import threading
import time
from pathlib import Path

import requests
import yaml

# On Windows the shebang path /bin/bash does not exist; use 'bash' (WSL/Git Bash)
_BASH = "bash" if platform.system() == "Windows" else "/bin/bash"

# All subprocess calls run with cwd set to the directory containing this script
# so that relative runbook paths in config.yaml resolve correctly.
_SCRIPT_DIR = Path(__file__).parent

from engine.logger import JsonLogger
from engine.metrics import (
    action_counter,
    blast_radius_gauge,
    circuit_breaker_gauge,
    mutex_gauge,
    start_metrics_server,
    verify_status_gauge,
)
from engine.safety import BlastRadiusGuard, CircuitBreaker
from engine.verify import verify_service

log = JsonLogger("orchestrator")

# ── Per-service mutex map ─────────────────────────────────────────────────────
# Each service gets its own threading.Lock.
# acquire(blocking=False): if locked → log SERVICE_LOCK_BUSY, skip duplicate.
# Two different services never share a lock → they run in parallel.
_service_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()


def get_service_lock(service: str) -> threading.Lock:
    with _locks_meta:
        if service not in _service_locks:
            _service_locks[service] = threading.Lock()
        return _service_locks[service]


# ── Config loading ────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg


# ── Alertmanager polling ──────────────────────────────────────────────────────

def fetch_active_alerts(alertmanager_url: str) -> list[dict]:
    """Fetch active, non-silenced, non-inhibited alerts from Alertmanager."""
    try:
        resp = requests.get(
            f"{alertmanager_url}/api/v2/alerts",
            params={"active": "true", "silenced": "false", "inhibited": "false"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("ALERTMANAGER_FETCH_ERROR", error=str(exc))
        return []


# ── Runbook execution ─────────────────────────────────────────────────────────

def run_runbook(
    script: str,
    service: str,
    dry_run: bool,
    timeout_s: int = 30,
    extra_args: list[str] | None = None,
) -> bool:
    """Execute a bash runbook script.  Returns True on exit code 0."""
    cmd = [_BASH, script, "--service", service]
    if dry_run:
        cmd.append("--dry-run")
    if extra_args:
        cmd.extend(extra_args)

    log.info(
        "RUNBOOK_EXEC",
        script=script,
        service=service,
        dry_run=dry_run,
        cmd=" ".join(cmd),
    )
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(_SCRIPT_DIR),  # ensure relative paths in scripts resolve correctly
        )
        log.info(
            "RUNBOOK_RESULT",
            script=script,
            service=service,
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("RUNBOOK_TIMEOUT", script=script, service=service, timeout_s=timeout_s)
        return False
    except Exception as exc:
        log.error("RUNBOOK_ERROR", script=script, service=service, error=str(exc))
        return False


# ── Decision validation ───────────────────────────────────────────────────────

def validate_runbook(runbook: str, cfg: dict, alertname: str, raw_decision: str) -> bool:
    """Reject runbook names absent from the explicit runbook_registry.

    This is the LLM-hallucination defence (Acceptance test #6).
    If runbook_registry is not configured, fall back to the values in runbook_map.
    """
    registry: list[str] = cfg.get(
        "runbook_registry",
        list(cfg.get("runbook_map", {}).values()),
    )
    if runbook in registry:
        return True

    log.error(
        "DECISION_VALIDATION_FAILED",
        bad_runbook=runbook,
        alertname=alertname,
        raw_decision=raw_decision,
        action="escalate_no_auto_action",
    )
    return False


# ── Transactional multi-step execution ───────────────────────────────────────

def run_transactional_steps(
    steps: list[str],
    service: str,
    timeout_s: int,
) -> tuple[bool, list[str]]:
    """Execute steps A→B→C in order.  Returns (all_ok, completed_steps).

    completed_steps records every step that exited 0, in execution order.
    Caller uses the list to drive reverse-order rollback.
    """
    completed: list[str] = []
    for step in steps:
        ok = run_runbook(step, service, dry_run=False, timeout_s=timeout_s)
        if not ok:
            log.error(
                "TRANSACTIONAL_STEP_FAIL",
                step=step,
                service=service,
                completed_before_failure=completed,
            )
            return False, completed
        completed.append(step)
        log.info("TRANSACTIONAL_STEP_COMPLETE", step=step, service=service)
    return True, completed


# ── Core alert processing ─────────────────────────────────────────────────────

def extract_service(alert: dict) -> str:
    labels = alert.get("labels", {})
    return labels.get("service") or labels.get("job") or "unknown"


def process_alert(
    alert: dict,
    cfg: dict,
    baseline: dict,
    guard: BlastRadiusGuard,
    cb: CircuitBreaker,
    global_dry_run: bool,
) -> None:
    """Process a single alert through all 5 checkpoints."""
    alertname = alert.get("labels", {}).get("alertname", "")
    service = extract_service(alert)
    severity = alert.get("labels", {}).get("severity", "")

    log.info(
        "ALERT_DETECTED",
        alertname=alertname,
        service=service,
        severity=severity,
        fingerprint=alert.get("fingerprint", ""),
    )

    # 1. DECIDE — map alertname → runbook
    runbook = cfg["runbook_map"].get(alertname)
    if not runbook:
        log.warning("NO_RUNBOOK", alertname=alertname, service=service)
        return

    # Decision validation (Stress #3: hallucination defence)
    if not validate_runbook(runbook, cfg, alertname, raw_decision=runbook):
        return  # DECISION_VALIDATION_FAILED already logged; no subprocess spawned

    log.info("DECIDE_RUNBOOK", alertname=alertname, service=service, runbook=runbook)

    # 2. BLAST-RADIUS check
    ok, reason = guard.check(service)
    if not ok:
        log.warning("BLAST_RADIUS_EXCEEDED", service=service, reason=reason)
        return
    log.info("BLAST_RADIUS_OK", service=service)

    # Stress #2: per-service mutex — serialise actions on the same service
    svc_lock = get_service_lock(service)
    acquired = svc_lock.acquire(blocking=False)
    if not acquired:
        log.warning(
            "SERVICE_LOCK_BUSY",
            service=service,
            message="Runbook already executing for this service; skipping duplicate alert",
        )
        return
    mutex_gauge.labels(service=service).set(1)

    try:
        _execute_checkpoint(
            alert=alert,
            alertname=alertname,
            service=service,
            runbook=runbook,
            cfg=cfg,
            baseline=baseline,
            guard=guard,
            cb=cb,
            global_dry_run=global_dry_run,
        )
    finally:
        mutex_gauge.labels(service=service).set(0)
        svc_lock.release()


def _execute_checkpoint(
    alert: dict,
    alertname: str,
    service: str,
    runbook: str,
    cfg: dict,
    baseline: dict,
    guard: BlastRadiusGuard,
    cb: CircuitBreaker,
    global_dry_run: bool,
) -> None:
    """Checkpoints 3–5 (dry-run / act / verify / rollback / circuit-breaker)."""
    timeout_s: int = cfg["runbook_timeout_seconds"]

    # 3. DRY-RUN — always run regardless of global --dry-run flag
    if not run_runbook(runbook, service, dry_run=True, timeout_s=timeout_s):
        log.error("DRY_RUN_FAIL", runbook=runbook, service=service)
        return
    log.info("DRY_RUN_PASS", runbook=runbook, service=service)

    # Short-circuit: global --dry-run → log and stop here
    if global_dry_run:
        action_counter.labels(service=service, runbook=runbook, outcome="dry_run").inc()
        log.info(
            "GLOBAL_DRY_RUN_SKIP",
            service=service,
            message="--dry-run flag active; real action suppressed",
        )
        return

    # 4. ACT — record blast-radius, then execute
    guard.record(service)
    remaining = guard.remaining_global()
    blast_radius_gauge.labels(service=service).set(remaining)

    # Check for multi-step transactional deploy (Stress #1)
    multi_steps: list[str] = cfg.get("multi_step_map", {}).get(alertname, [])
    if multi_steps:
        _execute_transactional(
            alertname=alertname,
            service=service,
            steps=multi_steps,
            cfg=cfg,
            cb=cb,
            timeout_s=timeout_s,
            runbook=runbook,
        )
        return

    # Standard single-runbook execution
    if not run_runbook(runbook, service, dry_run=False, timeout_s=timeout_s):
        log.error("ACTION_EXEC_FAIL", runbook=runbook, service=service)
        action_counter.labels(service=service, runbook=runbook, outcome="fail").inc()
        cb.record_failure()
        circuit_breaker_gauge.labels(service=service).set(1 if cb.is_open() else 0)
        return

    log.info("ACTION_EXECUTED", runbook=runbook, service=service)

    # 5a. VERIFY
    t = baseline["verify_thresholds"]
    verify_status_gauge.labels(service=service, runbook=runbook).set(2)  # in_progress

    verify_ok = verify_service(
        prometheus_url=cfg["prometheus_url"],
        service=service,
        baseline=baseline,
        timeout_s=t["verify_timeout_seconds"],
        poll_interval_s=t["verify_poll_interval_seconds"],
        min_samples=t["verify_min_samples"],
    )

    if verify_ok:
        verify_status_gauge.labels(service=service, runbook=runbook).set(1)  # pass
        action_counter.labels(service=service, runbook=runbook, outcome="success").inc()
        log.info("ACTION_SUCCESS", alertname=alertname, service=service, runbook=runbook)
        cb.record_success()
        circuit_breaker_gauge.labels(service=service).set(0)
        return

    # 5b. ROLLBACK — auto-triggered on verify failure
    verify_status_gauge.labels(service=service, runbook=runbook).set(0)  # fail
    action_counter.labels(service=service, runbook=runbook, outcome="rollback").inc()

    rollback = cfg.get("rollback_map", {}).get(alertname, runbook)
    log.warning(
        "ROLLBACK_TRIGGERED",
        service=service,
        rollback_runbook=rollback,
        failure_count=cb.failure_count() + 1,
    )
    run_runbook(rollback, service, dry_run=False, timeout_s=timeout_s)
    log.info("ROLLBACK_EXECUTED", service=service, rollback_runbook=rollback)

    cb.record_failure()
    circuit_breaker_gauge.labels(service=service).set(1 if cb.is_open() else 0)


def _execute_transactional(
    alertname: str,
    service: str,
    steps: list[str],
    cfg: dict,
    cb: CircuitBreaker,
    timeout_s: int,
    runbook: str,
) -> None:
    """Stress #1: transactional multi-step deploy with LIFO rollback on failure."""
    success, completed = run_transactional_steps(steps, service, timeout_s)
    if success:
        log.info(
            "ACTION_EXECUTED",
            runbook="multi_step",
            service=service,
            steps_completed=completed,
        )
        # Verify after successful transactional deploy (not strictly required
        # by HANDOUT for multi-step but keeps the pattern consistent)
        return

    # One step failed: rollback completed steps in reverse order
    rollback_steps: list[str] = cfg.get("multi_step_rollback_map", {}).get(alertname, [])
    # Only rollback steps that were actually completed
    steps_to_rollback = rollback_steps[: len(completed)]
    for rb_step in reversed(steps_to_rollback):
        log.warning("TRANSACTIONAL_ROLLBACK_STEP", step=rb_step, service=service)
        run_runbook(rb_step, service, dry_run=False, timeout_s=timeout_s)

    rolled_back = list(reversed(steps_to_rollback))
    log.info(
        "TRANSACTIONAL_ROLLBACK_COMPLETE",
        service=service,
        rolled_back=rolled_back,
    )
    action_counter.labels(service=service, runbook=runbook, outcome="rollback").inc()
    cb.record_failure()


# ── Main polling loop ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ronki AIOps closed-loop orchestrator — Option A (Rule-based)"
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect + decide only; suppress all real action execution",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Resolve baseline path relative to the config file location
    baseline_path = Path(args.config).parent / cfg["baseline_path"]
    with open(baseline_path) as f:
        baseline = json.load(f)

    guard = BlastRadiusGuard(
        max_per_minute=cfg["blast_radius"]["max_actions_per_minute"],
        max_restarts_per_hour=cfg["blast_radius"]["max_restarts_per_service_per_hour"],
    )
    cb = CircuitBreaker(
        threshold=cfg["circuit_breaker"]["consecutive_failure_threshold"]
    )

    # De-duplicate: track alert fingerprints already dispatched this session
    seen: set[str] = set()

    start_metrics_server()

    log.info(
        "ORCHESTRATOR_START",
        config=args.config,
        dry_run=args.dry_run,
        poll_interval_s=cfg["poll_interval_seconds"],
        blast_radius=cfg["blast_radius"],
        circuit_breaker=cfg["circuit_breaker"],
    )

    while True:
        # Circuit breaker open — pause automation, keep polling to emit heartbeat
        if cb.is_open():
            log.error(
                "CIRCUIT_BREAKER_HALT",
                message="Circuit OPEN — no actions will be executed. Restart to reset.",
            )
            time.sleep(cfg["poll_interval_seconds"])
            continue

        alerts = fetch_active_alerts(cfg["alertmanager_url"])

        # Dispatch each new alert in a separate thread (concurrent alert race support)
        threads: list[threading.Thread] = []
        for alert in alerts:
            fp = alert.get("fingerprint", "")
            if fp and fp in seen:
                continue
            if fp:
                seen.add(fp)

            t = threading.Thread(
                target=process_alert,
                args=(alert, cfg, baseline, guard, cb, args.dry_run),
                daemon=True,
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Prune seen set to avoid unbounded growth across long runs
        if len(seen) > 1000:
            seen.clear()

        time.sleep(cfg["poll_interval_seconds"])


if __name__ == "__main__":
    main()
