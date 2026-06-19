#!/usr/bin/env python3
"""Generate the deterministic 30-day incident data pack for the RCA
Forensics lab.

The data pack spans 2026-03-01 00:00 UTC to 2026-03-30 23:59 UTC and
contains five distinct incidents (I1 through I5). Each incident has a
different root cause shape and a different set of false-positive traps.
Metrics are sampled at 1-minute resolution throughout. Background log
volume is sparse (heartbeat lines) with dense streams during each
incident window.

Run from this directory:

    python _generator.py

Output is deterministic given the SEED below.
"""
from __future__ import annotations

import csv
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42
random.seed(SEED)

ROOT = Path(__file__).parent
(ROOT / "logs").mkdir(exist_ok=True)
(ROOT / "metrics").mkdir(exist_ok=True)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


START = utc(2026, 3, 1, 0, 0, 0)
END = utc(2026, 3, 30, 23, 59, 0)


# ────────────────────────────────────────────────────────────────────────────
# Incident windows
# ────────────────────────────────────────────────────────────────────────────

I1 = {"id": "I1", "label": "fx_api_retry_storm",
      "start": utc(2026, 3, 5, 14, 30), "end": utc(2026, 3, 5, 15, 5)}
I2 = {"id": "I2", "label": "inventory_memory_leak",
      "start": utc(2026, 3, 11, 2, 0), "end": utc(2026, 3, 11, 4, 30)}
I3 = {"id": "I3", "label": "feature_flag_slow_query",
      "start": utc(2026, 3, 17, 11, 15), "end": utc(2026, 3, 17, 12, 0)}
I4 = {"id": "I4", "label": "dns_az_split",
      "start": utc(2026, 3, 22, 9, 0), "end": utc(2026, 3, 22, 9, 45)}
I5 = {"id": "I5", "label": "mtls_cert_clock_skew",
      "start": utc(2026, 3, 27, 6, 0), "end": utc(2026, 3, 27, 6, 15)}

INCIDENTS = [I1, I2, I3, I4, I5]

# Non-incident events (ops noise students must NOT confuse for incidents)
ETL_DATES = [utc(2026, 3, 5, 14, 0), utc(2026, 3, 12, 14, 0),
             utc(2026, 3, 19, 14, 0), utc(2026, 3, 26, 14, 0)]
ETL_DURATION = timedelta(minutes=30)
BACKUP = {"start": utc(2026, 3, 14, 2, 0),
          "end": utc(2026, 3, 14, 3, 0)}


def in_etl(t: datetime) -> bool:
    return any(start <= t <= start + ETL_DURATION for start in ETL_DATES)


def in_backup(t: datetime) -> bool:
    return BACKUP["start"] <= t <= BACKUP["end"]


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def trace_id() -> str:
    return uuid.UUID(int=random.getrandbits(128)).hex[:16]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lerp(t: datetime, t0: datetime, t1: datetime,
         v0: float, v1: float) -> float:
    if t1 == t0:
        return v1
    frac = (t - t0).total_seconds() / (t1 - t0).total_seconds()
    return v0 + (v1 - v0) * clamp(frac, 0.0, 1.0)


def in_window(t: datetime, inc: dict) -> bool:
    return inc["start"] <= t <= inc["end"]


# ────────────────────────────────────────────────────────────────────────────
# topology.json
# ────────────────────────────────────────────────────────────────────────────

topology = {
    "platform": "ronki-shop",
    "services": [
        {"name": "frontend", "deps": ["api-gateway"]},
        {"name": "api-gateway", "deps": ["checkout-svc"]},
        {"name": "checkout-svc",
         "deps": ["payment-svc", "inventory-svc", "redis-cache"]},
        {"name": "payment-svc",
         "deps": ["rds-orders", "fx-api", "pp-api"]},
        {"name": "inventory-svc",
         "deps": ["rds-inventory", "warehouse-api"]},
        {"name": "redis-cache", "deps": []},
        {"name": "rds-orders", "deps": []},
        {"name": "rds-inventory", "deps": []},
    ],
    "third_party": [
        {"name": "fx-api", "endpoint": "https://fx.vendor.example/v1/convert",
         "owner": "external"},
        {"name": "pp-api", "endpoint": "https://pp.vendor.example/v2/charge",
         "owner": "external"},
        {"name": "warehouse-api",
         "endpoint": "https://wh.partner.example/api", "owner": "external"},
    ],
    "infrastructure": {
        "availability_zones": ["a", "b", "c"],
        "service_mesh": "internal-mesh-v1",
        "cert_rotation_interval_hours": 24,
    },
}
(ROOT / "topology.json").write_text(json.dumps(topology, indent=2) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# Metric profile functions
# ────────────────────────────────────────────────────────────────────────────
# Conventions:
#   - Pure functions of t. Stochastic noise uses module-level random.
#   - Each function applies its baseline first, then layers per-incident
#     perturbations. Effects (not causes) of an incident still appear.

NIGHT_HOURS = set(range(0, 6)) | set(range(22, 24))


def diurnal(t: datetime, base: float, peak_gain: float) -> float:
    """Smooth diurnal pattern: low at 03:00 UTC, peak at 15:00 UTC."""
    h = t.hour + t.minute / 60.0
    # cosine bump centered at hour=15 (peak), trough at hour=03
    import math
    phase = (h - 15) / 24.0
    val = base + peak_gain * (0.5 + 0.5 * math.cos(phase * 2 * math.pi))
    return val


def m_checkout_p99(t: datetime) -> float:
    base = diurnal(t, 200, 30) + random.uniform(-10, 10)
    if in_backup(t):
        return round(lerp(t, BACKUP["start"], BACKUP["end"],
                          base, 800) + random.uniform(-40, 40), 1)
    if in_window(t, I1):
        if t < I1["start"] + timedelta(minutes=2):
            return round(lerp(t, I1["start"], I1["start"] + timedelta(minutes=2),
                              base, 800), 1)
        if t < I1["start"] + timedelta(minutes=5):
            return round(lerp(t, I1["start"] + timedelta(minutes=2),
                              I1["start"] + timedelta(minutes=5), 800, 8000), 1)
        if t < I1["end"] - timedelta(minutes=5):
            return round(random.uniform(5000, 8000), 1)
        return round(lerp(t, I1["end"] - timedelta(minutes=5), I1["end"],
                          7500, 220), 1)
    if in_window(t, I2):
        return round(lerp(t, I2["start"], I2["end"], base, 3000)
                     + random.uniform(-100, 100), 1)
    if in_window(t, I3):
        return round(1500 + random.uniform(-150, 200), 1)
    if in_window(t, I4):
        return round(base + 40 + random.uniform(-10, 10), 1)
    if in_window(t, I5):
        return round(500 + random.uniform(-80, 80), 1)
    return round(base, 1)


def m_checkout_err(t: datetime) -> float:
    base = max(0.0, 0.005 + random.uniform(-0.001, 0.001))
    if in_window(t, I1):
        if t < I1["start"] + timedelta(minutes=2):
            return round(lerp(t, I1["start"], I1["start"] + timedelta(minutes=2),
                              base, 0.04), 4)
        if t < I1["end"] - timedelta(minutes=5):
            return round(random.uniform(0.10, 0.12), 4)
        return round(lerp(t, I1["end"] - timedelta(minutes=5), I1["end"],
                          0.11, base), 4)
    if in_window(t, I2):
        return round(lerp(t, I2["start"], I2["end"], base, 0.06), 4)
    if in_window(t, I3):
        return round(random.uniform(0.025, 0.035), 4)
    if in_window(t, I4):
        return round(random.uniform(0.035, 0.045), 4)
    if in_window(t, I5):
        if t < I5["end"] - timedelta(minutes=2):
            return round(random.uniform(0.30, 0.34), 4)
        return round(lerp(t, I5["end"] - timedelta(minutes=2), I5["end"],
                          0.33, base), 4)
    return round(base, 4)


def m_payment_mem(t: datetime) -> float:
    base = 60 + random.uniform(-1, 1)
    if in_window(t, I1):
        if t < I1["end"] - timedelta(minutes=5):
            return round(lerp(t, I1["start"], I1["end"] - timedelta(minutes=5),
                              60, 92), 2)
        return round(lerp(t, I1["end"] - timedelta(minutes=5),
                          I1["end"] + timedelta(minutes=2), 90, 60), 2)
    return round(base, 2)


def m_payment_pool(t: datetime) -> int:
    base = 10 + random.uniform(-2, 2)
    if in_window(t, I1):
        if t < I1["end"] - timedelta(minutes=5):
            return int(lerp(t, I1["start"], I1["end"] - timedelta(minutes=5),
                            10, 200))
        return int(lerp(t, I1["end"] - timedelta(minutes=5), I1["end"],
                        200, 10))
    if in_window(t, I3):
        return int(lerp(t, I3["start"], I3["start"] + timedelta(minutes=8),
                        10, 48) + random.uniform(-2, 2))
    return int(base)


def m_payment_retries(t: datetime) -> int:
    base = 2 + random.uniform(-1, 1)
    if in_window(t, I1):
        return int(300 + random.uniform(-30, 30))
    if in_window(t, I4):
        return int(80 + random.uniform(-15, 15))
    return int(base)


def m_rds_cpu(t: datetime) -> float:
    base = diurnal(t, 25, 10) + random.uniform(-3, 3)
    # ETL non-incident: weekly ~30 min CPU bump to ~60%, no alert
    if in_etl(t):
        base = max(base, 58 + random.uniform(-3, 3))
    # Backup non-incident on 03-14 02:00-03:00: bump to ~70%
    if in_backup(t):
        base = max(base, 70 + random.uniform(-2, 2))
    if in_window(t, I1):
        rise = I1["start"] + timedelta(minutes=4)
        if t < rise:
            return round(base, 1)
        if t < rise + timedelta(minutes=2):
            return round(lerp(t, rise, rise + timedelta(minutes=2),
                              base, 88), 1)
        if t < I1["end"] - timedelta(minutes=5):
            return round(random.uniform(85, 90), 1)
        return round(lerp(t, I1["end"] - timedelta(minutes=5), I1["end"],
                          87, base), 1)
    if in_window(t, I3):
        return round(random.uniform(70, 78), 1)
    return round(base, 1)


def m_rds_audit(t: datetime) -> int:
    base = 30 + random.uniform(-5, 5)
    if in_window(t, I1):
        return int(3000 + random.uniform(-200, 200))
    return int(base)


def m_rds_query_p99(t: datetime) -> float:
    base = 18 + random.uniform(-3, 3)
    if in_window(t, I3):
        return round(random.uniform(3000, 4200), 1)
    if in_window(t, I1):
        return round(60 + random.uniform(-10, 20), 1)
    return round(base, 1)


def m_redis_hit(t: datetime) -> float:
    base = 0.95 + random.uniform(-0.01, 0.01)
    if in_window(t, I1):
        drop = I1["start"] + timedelta(minutes=3)
        if t < drop:
            return round(base, 4)
        if t < drop + timedelta(minutes=2):
            return round(lerp(t, drop, drop + timedelta(minutes=2),
                              0.95, 0.40), 4)
        if t < I1["end"] - timedelta(minutes=5):
            return round(0.40 + random.uniform(-0.02, 0.02), 4)
        return round(lerp(t, I1["end"] - timedelta(minutes=5), I1["end"],
                          0.45, 0.94), 4)
    return round(base, 4)


def m_redis_evict(t: datetime) -> int:
    return int(5 + random.uniform(-2, 2))


def m_inv_p99(t: datetime) -> float:
    base = 40 + random.uniform(-5, 5)
    if in_window(t, I2):
        return round(lerp(t, I2["start"], I2["end"], base, 300)
                     + random.uniform(-20, 20), 1)
    return round(base, 1)


def m_inv_warehouse_health(t: datetime) -> int:
    return 1


def m_inv_memory(t: datetime) -> float:
    base = 200 + random.uniform(-10, 10)
    if in_window(t, I2):
        return round(lerp(t, I2["start"], I2["end"], 220, 4000)
                     + random.uniform(-50, 50), 1)
    return round(base, 1)


def m_inv_oom(t: datetime) -> int:
    if in_window(t, I2):
        if (I2["end"] - timedelta(minutes=15) <= t <=
                I2["end"] - timedelta(minutes=5)):
            return 1 if random.random() < 0.25 else 0
    return 0


def m_fx_5xx(t: datetime) -> int:
    if in_window(t, I1):
        return int(50 + random.uniform(-5, 5))
    return 0


def m_payment_az_a_err(t: datetime) -> float:
    return round(max(0.005 + random.uniform(-0.001, 0.001), 0), 4)


def m_payment_az_b_err(t: datetime) -> float:
    return round(max(0.005 + random.uniform(-0.001, 0.001), 0), 4)


def m_payment_az_c_err(t: datetime) -> float:
    base = 0.005 + random.uniform(-0.001, 0.001)
    if in_window(t, I4):
        return round(random.uniform(0.28, 0.34), 4)
    return round(max(base, 0), 4)


def m_mtls_failures(t: datetime) -> int:
    if in_window(t, I5):
        if t < I5["end"] - timedelta(minutes=2):
            return int(500 + random.uniform(-50, 50))
        return int(lerp(t, I5["end"] - timedelta(minutes=2), I5["end"],
                        450, 0))
    return 0


def m_frontend_rps(t: datetime) -> float:
    base = diurnal(t, 1200, 600) + random.uniform(-40, 40)
    if in_window(t, I2):
        if (I2["start"] + timedelta(minutes=50) <= t <=
                I2["start"] + timedelta(minutes=90)):
            base += 220
    return round(base, 1)


METRICS = [
    ("checkout_p99_ms.csv", m_checkout_p99),
    ("checkout_error_rate.csv", m_checkout_err),
    ("payment_memory_pct.csv", m_payment_mem),
    ("payment_conn_pool_active.csv", m_payment_pool),
    ("payment_retries_per_min.csv", m_payment_retries),
    ("payment_az_a_error_rate.csv", m_payment_az_a_err),
    ("payment_az_b_error_rate.csv", m_payment_az_b_err),
    ("payment_az_c_error_rate.csv", m_payment_az_c_err),
    ("rds_cpu_pct.csv", m_rds_cpu),
    ("rds_audit_writes_per_min.csv", m_rds_audit),
    ("rds_query_p99_ms.csv", m_rds_query_p99),
    ("redis_hit_rate.csv", m_redis_hit),
    ("redis_evictions_per_min.csv", m_redis_evict),
    ("inventory_p99_ms.csv", m_inv_p99),
    ("inventory_warehouse_health.csv", m_inv_warehouse_health),
    ("inventory_memory_mb.csv", m_inv_memory),
    ("inventory_oom_kills_per_min.csv", m_inv_oom),
    ("fx_api_5xx_per_min.csv", m_fx_5xx),
    ("mtls_handshake_failures_per_min.csv", m_mtls_failures),
    ("frontend_req_rate.csv", m_frontend_rps),
]


def write_metrics() -> None:
    for path, fn in METRICS:
        with open(ROOT / "metrics" / path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "value"])
            t = START
            while t <= END:
                w.writerow([iso(t), fn(t)])
                t += timedelta(minutes=1)


# ────────────────────────────────────────────────────────────────────────────
# Logs
# ────────────────────────────────────────────────────────────────────────────

def write_jsonl(name: str, lines: list[dict]) -> None:
    lines.sort(key=lambda r: r["ts"])
    with open(ROOT / "logs" / name, "w") as f:
        for r in lines:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def ts_jitter(t: datetime, jitter_ms: int = 999) -> str:
    return iso(t + timedelta(milliseconds=random.randint(0, jitter_ms)))


def az_for(i: int) -> str:
    return ["a", "b", "c"][i % 3]


def background_heartbeats(service: str, component: str,
                          msg: str, every: timedelta) -> list[dict]:
    """One INFO heartbeat every `every` for the full period."""
    out = []
    t = START
    while t < END:
        out.append({"ts": ts_jitter(t), "level": "INFO", "service": service,
                    "component": component, "msg": msg,
                    "extra": {"alive": True}})
        t += every
    return out


# Per-service log generators (background + incident contributions)

def gen_frontend() -> None:
    out = background_heartbeats("frontend", "request",
                                "heartbeat", timedelta(minutes=5))
    for _ in range(120):
        t = I1["start"] + timedelta(seconds=random.randint(120, 1800))
        out.append({"ts": ts_jitter(t), "level": "ERROR", "service": "frontend",
                    "component": "request", "msg": "upstream_5xx",
                    "trace_id": trace_id(),
                    "extra": {"path": "/checkout", "status": 503,
                              "duration_ms": random.randint(7000, 9000)}})
    for _ in range(80):
        t = I2["start"] + timedelta(seconds=random.randint(600, 8400))
        out.append({"ts": ts_jitter(t), "level": "WARN", "service": "frontend",
                    "component": "request", "msg": "upstream_slow",
                    "trace_id": trace_id(),
                    "extra": {"path": "/cart", "duration_ms":
                              random.randint(1500, 3000)}})
    for _ in range(40):
        t = I3["start"] + timedelta(seconds=random.randint(60, 2700))
        out.append({"ts": ts_jitter(t), "level": "WARN", "service": "frontend",
                    "component": "request", "msg": "upstream_slow",
                    "trace_id": trace_id(),
                    "extra": {"path": "/checkout",
                              "duration_ms": random.randint(1200, 1800)}})
    for _ in range(60):
        t = I4["start"] + timedelta(seconds=random.randint(0, 2700))
        out.append({"ts": ts_jitter(t), "level": "ERROR", "service": "frontend",
                    "component": "request", "msg": "upstream_5xx",
                    "trace_id": trace_id(),
                    "extra": {"path": "/checkout", "status": 503,
                              "duration_ms": random.randint(200, 600)}})
    for _ in range(50):
        t = I5["start"] + timedelta(seconds=random.randint(0, 780))
        out.append({"ts": ts_jitter(t), "level": "ERROR", "service": "frontend",
                    "component": "request", "msg": "upstream_5xx",
                    "trace_id": trace_id(),
                    "extra": {"path": "/checkout", "status": 502,
                              "duration_ms": random.randint(400, 800)}})
    write_jsonl("frontend.jsonl", out)


def gen_api_gateway() -> None:
    out = background_heartbeats("api-gateway", "router",
                                "heartbeat", timedelta(minutes=5))
    for inc, count in [(I1, 80), (I2, 40), (I3, 30), (I4, 50), (I5, 40)]:
        for _ in range(count):
            t = inc["start"] + timedelta(
                seconds=random.randint(0,
                                       int((inc["end"] - inc["start"])
                                           .total_seconds())))
            out.append({"ts": ts_jitter(t), "level": "WARN",
                        "service": "api-gateway", "component": "router",
                        "msg": "downstream_slow",
                        "trace_id": trace_id(),
                        "extra": {"route": "checkout-svc",
                                  "duration_ms": random.randint(800, 8000)}})
    write_jsonl("api-gateway.jsonl", out)


def gen_checkout() -> None:
    out = background_heartbeats("checkout-svc", "handler",
                                "heartbeat", timedelta(minutes=5))

    # I1: circuit breaker + downstream 5xx
    out.append({"ts": iso(I1["start"] + timedelta(minutes=5)),
                "level": "WARN", "service": "checkout-svc",
                "component": "circuit_breaker",
                "msg": "circuit_breaker_open",
                "extra": {"target": "payment-svc",
                          "consecutive_failures": 20,
                          "open_for_ms": 30000}})
    for _ in range(220):
        t = I1["start"] + timedelta(seconds=random.randint(180, 1800))
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "checkout-svc",
                    "component": "payment_client",
                    "msg": "downstream_5xx",
                    "trace_id": trace_id(),
                    "extra": {"target": "payment-svc", "status": 503,
                              "duration_ms": random.randint(5000, 8000)}})
    for _ in range(50):
        t = I1["start"] + timedelta(seconds=random.randint(200, 1800))
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "checkout-svc",
                    "component": "circuit_breaker",
                    "msg": "fast_fail",
                    "extra": {"target": "payment-svc"}})

    # I3: downstream timeouts on payment-svc, customer-report style entries
    for _ in range(70):
        t = I3["start"] + timedelta(seconds=random.randint(60, 2700))
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "checkout-svc",
                    "component": "payment_client",
                    "msg": "downstream_timeout",
                    "trace_id": trace_id(),
                    "extra": {"target": "payment-svc",
                              "duration_ms": random.randint(3000, 4500),
                              "timeout_ms": 4000}})

    # I4: connection_refused propagated through checkout for AZ-c traffic
    for _ in range(60):
        t = I4["start"] + timedelta(seconds=random.randint(0, 2700))
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "checkout-svc",
                    "component": "payment_client",
                    "msg": "downstream_connection_refused",
                    "trace_id": trace_id(),
                    "extra": {"target": "payment-svc",
                              "duration_ms": random.randint(50, 200)}})

    # I5: TLS handshake errors — the smoking gun
    for i in range(140):
        t = I5["start"] + timedelta(seconds=random.randint(0, 780))
        not_before = utc(2026, 3, 27, 6, 0, 15)
        current = t  # observer clock (skewed behind by ~30s)
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "checkout-svc",
                    "component": "mtls_client",
                    "msg": "TLS_handshake_error_certificate_not_yet_valid",
                    "trace_id": trace_id(),
                    "extra": {"target": "payment-svc",
                              "not_before": iso(not_before),
                              "current_time": iso(current
                                                  - timedelta(seconds=27)),
                              "delta_seconds": -27}})
    out.append({"ts": iso(I5["end"]),
                "level": "INFO", "service": "checkout-svc",
                "component": "mtls_client",
                "msg": "TLS_handshake_recovered",
                "extra": {"target": "payment-svc"}})

    write_jsonl("checkout-svc.jsonl", out)


def gen_payment() -> None:
    out = background_heartbeats("payment-svc", "handler",
                                "heartbeat", timedelta(minutes=5))

    # I1: fx-api 503 burst + retries + pool exhaustion + GC
    t = I1["start"]
    while t < I1["end"]:
        tid = trace_id()
        retry = random.choices([1, 2, 3], weights=[6, 3, 1])[0]
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "payment-svc", "component": "fx_client",
                    "msg": "upstream_call",
                    "trace_id": tid,
                    "extra": {"endpoint": "fx-api", "status": 503,
                              "retry_attempt": retry,
                              "duration_ms": random.randint(800, 3500),
                              "az": az_for(random.randint(0, 8))}})
        if retry == 3:
            out.append({"ts": ts_jitter(t + timedelta(milliseconds=400)),
                        "level": "ERROR", "service": "payment-svc",
                        "component": "fx_client",
                        "msg": "fx_call_failed_after_retries",
                        "trace_id": tid,
                        "extra": {"endpoint": "fx-api", "max_retries": 3,
                                  "total_duration_ms":
                                  random.randint(3000, 8000)}})
        t += timedelta(seconds=random.uniform(1.0, 1.6))

    t = I1["start"] + timedelta(minutes=2)
    while t < I1["end"]:
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "payment-svc", "component": "http_client",
                    "msg": "conn_pool_acquire_timeout",
                    "extra": {"pool": "outbound-http",
                              "active": random.randint(180, 200),
                              "max": 200,
                              "wait_ms": random.randint(1500, 5000)}})
        t += timedelta(seconds=random.uniform(3.0, 7.0))

    t = I1["start"] + timedelta(minutes=8)
    while t < I1["end"]:
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "payment-svc", "component": "gc",
                    "msg": "gc_pressure_high",
                    "extra": {"heap_pct": round(random.uniform(82, 92), 1),
                              "pause_ms": random.randint(40, 120)}})
        t += timedelta(seconds=random.uniform(15, 30))

    out.append({"ts": iso(I1["end"] + timedelta(seconds=30)),
                "level": "INFO", "service": "payment-svc",
                "component": "fx_client",
                "msg": "upstream_recovered",
                "extra": {"endpoint": "fx-api", "consecutive_2xx": 50}})

    # I2: GC pressure as noisy neighbor (the trap)
    t = I2["start"] + timedelta(minutes=90)
    while t < I2["end"]:
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "payment-svc", "component": "gc",
                    "msg": "gc_pressure_high",
                    "extra": {"heap_pct": round(random.uniform(62, 70), 1),
                              "pause_ms": random.randint(20, 50),
                              "host_neighbor_inventory_mem_mb":
                              random.randint(3000, 4000)}})
        t += timedelta(seconds=random.uniform(30, 90))

    # I3: loyalty_client + slow queries — the smoking gun
    out.append({"ts": iso(I3["start"] + timedelta(seconds=2)),
                "level": "INFO", "service": "payment-svc",
                "component": "feature_flag",
                "msg": "feature_flag_observed",
                "extra": {"flag": "enable_loyalty_recommendations",
                          "value": True}})
    t = I3["start"] + timedelta(seconds=15)
    while t < I3["end"]:
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "payment-svc", "component": "loyalty_client",
                    "msg": "slow_query",
                    "trace_id": trace_id(),
                    "extra": {"table": "transactions",
                              "where": "user_id = ?",
                              "order_by": "ts DESC",
                              "duration_ms":
                              random.randint(3200, 4200),
                              "rows_scanned":
                              random.randint(80000, 220000)}})
        t += timedelta(seconds=random.uniform(2.0, 4.0))

    # I4: connection_refused to pp-api from AZ-c only
    t = I4["start"]
    while t < I4["end"]:
        az = az_for(random.randint(0, 8))
        if random.random() < 0.34:
            az = "c"
        if az == "c":
            out.append({"ts": ts_jitter(t), "level": "ERROR",
                        "service": "payment-svc",
                        "component": "payment_processor_client",
                        "msg": "connection_refused",
                        "trace_id": trace_id(),
                        "extra": {"endpoint": "pp-api",
                                  "remote_host": "pp.vendor.example",
                                  "az": "c",
                                  "duration_ms":
                                  random.randint(20, 80),
                                  "resolved_ip": "203.0.113.10"}})
        else:
            out.append({"ts": ts_jitter(t), "level": "INFO",
                        "service": "payment-svc",
                        "component": "payment_processor_client",
                        "msg": "charge_ok",
                        "trace_id": trace_id(),
                        "extra": {"endpoint": "pp-api", "az": az,
                                  "duration_ms":
                                  random.randint(80, 140),
                                  "resolved_ip":
                                  random.choice(["198.51.100.20",
                                                 "198.51.100.21"])}})
        t += timedelta(seconds=random.uniform(0.4, 1.2))

    out.append({"ts": iso(I4["end"]),
                "level": "INFO", "service": "payment-svc",
                "component": "payment_processor_client",
                "msg": "az_c_resolver_refreshed",
                "extra": {"endpoint": "pp-api",
                          "new_ips": ["198.51.100.20", "198.51.100.21"],
                          "az": "c"}})

    write_jsonl("payment-svc.jsonl", out)


def gen_inventory() -> None:
    out = background_heartbeats("inventory-svc", "handler",
                                "heartbeat", timedelta(minutes=5))

    # I1 trap: one stale-keepalive warning (single line)
    out.append({"ts": iso(I1["start"] + timedelta(minutes=1, seconds=15)),
                "level": "WARN", "service": "inventory-svc",
                "component": "warehouse_client",
                "msg": "network_timeout",
                "trace_id": trace_id(),
                "extra": {"endpoint": "warehouse-api",
                          "remote": "10.0.5.12", "duration_ms": 5000,
                          "note": "stale_keepalive_connection_closed"}})

    # I2: nightly batch + cache_entry_added — smoking gun
    out.append({"ts": iso(I2["start"]),
                "level": "INFO", "service": "inventory-svc",
                "component": "scheduler",
                "msg": "nightly_sku_sync started",
                "extra": {"job_id": "sku-sync-20260311",
                          "expected_sku_count": 250000}})
    big_sku_count = 0
    t = I2["start"] + timedelta(seconds=5)
    while t < I2["end"]:
        is_big = random.random() < 0.05
        if is_big:
            big_sku_count += 1
            sku = f"LRG-{big_sku_count:05d}"
            out.append({"ts": ts_jitter(t), "level": "INFO",
                        "service": "inventory-svc",
                        "component": "response_cache",
                        "msg": "cache_entry_added",
                        "extra": {"sku": sku,
                                  "size_bytes": 5242880,
                                  "ttl_seconds": None,
                                  "current_cache_entries": big_sku_count,
                                  "current_cache_bytes":
                                  big_sku_count * 5242880}})
        t += timedelta(seconds=random.uniform(2.0, 4.0))

    for _ in range(2):
        t = I2["end"] - timedelta(minutes=random.randint(5, 15))
        out.append({"ts": ts_jitter(t), "level": "ERROR",
                    "service": "inventory-svc",
                    "component": "lifecycle",
                    "msg": "OOM_killed",
                    "extra": {"pid": random.randint(1000, 9999),
                              "heap_mb_before_kill":
                              random.randint(3500, 4000)}})

    write_jsonl("inventory-svc.jsonl", out)


def gen_rds() -> None:
    out = background_heartbeats("rds-orders", "query",
                                "heartbeat", timedelta(minutes=5))

    # Non-incident: weekly ETL on Thursdays 14:00-14:30
    for etl_start in ETL_DATES:
        out.append({"ts": iso(etl_start), "level": "INFO",
                    "service": "rds-orders", "component": "scheduler",
                    "msg": "scheduled_job_started",
                    "extra": {"job": "weekly_finance_etl",
                              "duration_estimate_min": 30,
                              "recurrence": "weekly"}})
        out.append({"ts": iso(etl_start + ETL_DURATION),
                    "level": "INFO", "service": "rds-orders",
                    "component": "scheduler",
                    "msg": "scheduled_job_completed",
                    "extra": {"job": "weekly_finance_etl",
                              "actual_duration_min": 30}})

    # Non-incident: one-time backup window on 03-14
    out.append({"ts": iso(BACKUP["start"]), "level": "INFO",
                "service": "rds-orders", "component": "maintenance",
                "msg": "backup_window_started",
                "extra": {"window_minutes": 60,
                          "scheduled_via": "deploy_log",
                          "type": "full_database_snapshot"}})
    out.append({"ts": iso(BACKUP["end"]), "level": "INFO",
                "service": "rds-orders", "component": "maintenance",
                "msg": "backup_window_completed",
                "extra": {"duration_minutes": 60, "status": "ok"}})

    # I1: audit_log INSERT amplification
    t = I1["start"]
    while t < I1["end"]:
        out.append({"ts": ts_jitter(t), "level": "INFO",
                    "service": "rds-orders", "component": "query",
                    "msg": "sql_executed",
                    "extra": {"table": "audit_log", "op": "INSERT",
                              "source_service": "payment-svc",
                              "duration_ms": random.randint(2, 6)}})
        t += timedelta(seconds=random.uniform(0.5, 1.5))

    t = I1["start"] + timedelta(minutes=5)
    while t < I1["end"]:
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "rds-orders", "component": "query",
                    "msg": "slow_query",
                    "extra": {"table": "audit_log", "op": "INSERT",
                              "duration_ms": random.randint(1500, 3500),
                              "cpu_pct": round(random.uniform(85, 92),
                                               1)}})
        t += timedelta(seconds=random.uniform(10, 20))

    # I3: unindexed query slow_query lines — supporting evidence
    t = I3["start"] + timedelta(seconds=20)
    while t < I3["end"]:
        out.append({"ts": ts_jitter(t), "level": "WARN",
                    "service": "rds-orders", "component": "query",
                    "msg": "slow_query",
                    "extra": {"table": "transactions",
                              "op": "SELECT",
                              "where": "user_id = ?",
                              "duration_ms":
                              random.randint(3000, 4200),
                              "rows_examined":
                              random.randint(80000, 220000),
                              "uses_index": False,
                              "source_service": "payment-svc"}})
        t += timedelta(seconds=random.uniform(2.0, 4.0))

    write_jsonl("rds-orders.jsonl", out)


def gen_redis() -> None:
    out = []
    t = START
    while t < END:
        out.append({"ts": ts_jitter(t), "level": "INFO",
                    "service": "redis-cache", "component": "stats",
                    "msg": "stats_tick",
                    "extra": {"connected_clients":
                              random.randint(20, 30),
                              "used_memory_mb":
                              random.randint(450, 470),
                              "ops_per_sec":
                              random.randint(8000, 12000)}})
        t += timedelta(minutes=10)
    write_jsonl("redis-cache.jsonl", out)


# ────────────────────────────────────────────────────────────────────────────
# alerts.json
# ────────────────────────────────────────────────────────────────────────────

def write_alerts() -> None:
    A = []
    # I1
    A += [
        {"id": "A-001", "name": "CheckoutP99High", "service": "checkout-svc",
         "severity": "critical",
         "fired_at": iso(I1["start"] + timedelta(minutes=2, seconds=15)),
         "summary": "p99 latency above 1500 ms for 2 minutes",
         "labels": {"metric": "checkout_p99_ms", "threshold_ms": 1500}},
        {"id": "A-002", "name": "CheckoutErrorRateHigh",
         "service": "checkout-svc", "severity": "critical",
         "fired_at": iso(I1["start"] + timedelta(minutes=3)),
         "summary": "error rate above 5% for 1 minute",
         "labels": {"metric": "checkout_error_rate", "threshold": 0.05}},
        {"id": "A-003", "name": "RedisHitRateLow", "service": "redis-cache",
         "severity": "warning",
         "fired_at": iso(I1["start"] + timedelta(minutes=4, seconds=30)),
         "summary": "hit rate below 70% for 1 minute",
         "labels": {"metric": "redis_hit_rate", "threshold": 0.70}},
        {"id": "A-004", "name": "PaymentConnPoolSaturated",
         "service": "payment-svc", "severity": "critical",
         "fired_at": iso(I1["start"] + timedelta(minutes=5)),
         "summary": "outbound HTTP connection pool > 90% utilization",
         "labels": {"metric": "payment_conn_pool_active",
                    "threshold_pct": 90, "pool_max": 200}},
        {"id": "A-005", "name": "RdsCpuHigh", "service": "rds-orders",
         "severity": "warning",
         "fired_at": iso(I1["start"] + timedelta(minutes=6)),
         "summary": "CPU above 80% for 1 minute",
         "labels": {"metric": "rds_cpu_pct", "threshold_pct": 80}},
        {"id": "A-006", "name": "PaymentMemoryHigh", "service": "payment-svc",
         "severity": "warning",
         "fired_at": iso(I1["start"] + timedelta(minutes=8)),
         "summary": "heap usage above 85%",
         "labels": {"metric": "payment_memory_pct", "threshold_pct": 85}},
        {"id": "A-007", "name": "SyntheticCheckoutFailing",
         "service": "checkout-svc", "severity": "critical",
         "fired_at": iso(I1["start"] + timedelta(minutes=6)),
         "summary": "external synthetic check for /checkout failed 3 times",
         "labels": {"check": "synthetic_checkout_v2"}},
        {"id": "A-008", "name": "FxApi5xxObserved", "service": "payment-svc",
         "severity": "info",
         "fired_at": iso(I1["start"] + timedelta(minutes=9)),
         "summary": "non-zero 5xx from fx-api over 5 min window",
         "labels": {"metric": "fx_api_5xx_per_min", "threshold": 1}},
    ]
    # I2
    A += [
        {"id": "A-101", "name": "InventoryP99High", "service": "inventory-svc",
         "severity": "warning",
         "fired_at": iso(I2["start"] + timedelta(minutes=45)),
         "summary": "p99 latency above 150 ms for 5 minutes",
         "labels": {"metric": "inventory_p99_ms", "threshold_ms": 150}},
        {"id": "A-102", "name": "InventoryMemoryHigh",
         "service": "inventory-svc", "severity": "warning",
         "fired_at": iso(I2["start"] + timedelta(hours=1)),
         "summary": "RSS above 2000 MB and growing",
         "labels": {"metric": "inventory_memory_mb", "threshold_mb": 2000}},
        {"id": "A-103", "name": "CheckoutErrorRateHigh",
         "service": "checkout-svc", "severity": "warning",
         "fired_at": iso(I2["start"] + timedelta(hours=1, minutes=20)),
         "summary": "error rate above 3% for 5 minutes",
         "labels": {"metric": "checkout_error_rate", "threshold": 0.03}},
        {"id": "A-104", "name": "InventoryOOMKilled",
         "service": "inventory-svc", "severity": "critical",
         "fired_at": iso(I2["end"] - timedelta(minutes=12)),
         "summary": "container OOM-killed",
         "labels": {"metric": "inventory_oom_kills_per_min"}},
    ]
    # I3
    A += [
        {"id": "A-201", "name": "CheckoutP99High", "service": "checkout-svc",
         "severity": "warning",
         "fired_at": iso(I3["start"] + timedelta(minutes=3)),
         "summary": "p99 latency above 1000 ms for 2 minutes",
         "labels": {"metric": "checkout_p99_ms", "threshold_ms": 1000}},
        {"id": "A-202", "name": "RdsQueryP99High", "service": "rds-orders",
         "severity": "warning",
         "fired_at": iso(I3["start"] + timedelta(minutes=4)),
         "summary": "p99 query latency above 1000 ms",
         "labels": {"metric": "rds_query_p99_ms", "threshold_ms": 1000}},
        {"id": "A-203", "name": "PaymentConnPoolSaturated",
         "service": "payment-svc", "severity": "warning",
         "fired_at": iso(I3["start"] + timedelta(minutes=10)),
         "summary": "outbound HTTP connection pool > 90% utilization",
         "labels": {"metric": "payment_conn_pool_active",
                    "threshold_pct": 90, "pool_max": 50}},
        {"id": "A-204", "name": "RdsCpuHigh", "service": "rds-orders",
         "severity": "info",
         "fired_at": iso(I3["start"] + timedelta(minutes=6)),
         "summary": "CPU above 65% for 5 minutes",
         "labels": {"metric": "rds_cpu_pct", "threshold_pct": 65}},
    ]
    # I4
    A += [
        {"id": "A-301", "name": "CheckoutErrorRateHigh",
         "service": "checkout-svc", "severity": "warning",
         "fired_at": iso(I4["start"] + timedelta(minutes=2)),
         "summary": "error rate above 3% for 1 minute",
         "labels": {"metric": "checkout_error_rate", "threshold": 0.03}},
        {"id": "A-302", "name": "PaymentRegionalErrorRateHigh",
         "service": "payment-svc", "severity": "critical",
         "fired_at": iso(I4["start"] + timedelta(minutes=3)),
         "summary": "per-AZ error rate exceeded 20% on one or more zones",
         "labels": {"metric": "payment_per_az_error_rate",
                    "threshold": 0.20}},
        {"id": "A-303", "name": "PaymentRetriesElevated",
         "service": "payment-svc", "severity": "info",
         "fired_at": iso(I4["start"] + timedelta(minutes=5)),
         "summary": "outbound retries elevated",
         "labels": {"metric": "payment_retries_per_min"}},
    ]
    # I5
    A += [
        {"id": "A-401", "name": "MtlsHandshakeFailureSpike",
         "service": "checkout-svc", "severity": "critical",
         "fired_at": iso(I5["start"] + timedelta(seconds=45)),
         "summary": "mTLS handshake failures > 100/min",
         "labels": {"metric": "mtls_handshake_failures_per_min",
                    "threshold_per_min": 100}},
        {"id": "A-402", "name": "CheckoutErrorRateHigh",
         "service": "checkout-svc", "severity": "critical",
         "fired_at": iso(I5["start"] + timedelta(minutes=1)),
         "summary": "error rate above 20%",
         "labels": {"metric": "checkout_error_rate", "threshold": 0.20}},
        {"id": "A-403", "name": "SyntheticCheckoutFailing",
         "service": "checkout-svc", "severity": "critical",
         "fired_at": iso(I5["start"] + timedelta(minutes=2)),
         "summary": "external synthetic check failed 3 times",
         "labels": {"check": "synthetic_checkout_v2"}},
    ]
    # Non-incident alert during backup window (resolves at end of window)
    A += [
        {"id": "A-501", "name": "SyntheticCheckoutFailing",
         "service": "checkout-svc", "severity": "warning",
         "fired_at": iso(BACKUP["start"] + timedelta(minutes=15)),
         "summary": "synthetic check elevated latency",
         "labels": {"check": "synthetic_checkout_v2"}},
    ]
    A.sort(key=lambda a: a["fired_at"])
    (ROOT / "alerts.json").write_text(json.dumps(A, indent=2) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# deploy_log.json — mix of innocent and trap entries
# ────────────────────────────────────────────────────────────────────────────

def write_deploy_log() -> None:
    E = [
        {"ts": iso(utc(2026, 2, 28, 9, 0)),
         "type": "deploy", "service": "frontend", "version": "v3.1.0",
         "changes": ["bundle size reduction"], "actor": "ci-pipeline"},
        {"ts": iso(utc(2026, 3, 1, 9, 30)),
         "type": "maintenance", "service": "redis-cache",
         "changes": ["rolling restart for kernel patch"],
         "actor": "platform-team"},
        # I1 trap: inventory deploy 32 min before I1
        {"ts": iso(I1["start"] - timedelta(minutes=32)),
         "type": "deploy", "service": "inventory-svc", "version": "v2.4.1",
         "changes": ["config: warehouse_timeout_ms 5000 -> 3000"],
         "actor": "ci-pipeline"},
        # Innocent: payment flag long-standing
        {"ts": iso(I1["start"] - timedelta(hours=2)),
         "type": "config", "service": "payment-svc",
         "changes": ["feature flag fx_retry_jitter set to disabled"],
         "actor": "platform-team",
         "note": "flag has been in this state since 2026-02-01"},
        # Innocent: payment deploy on day 9
        {"ts": iso(utc(2026, 3, 9, 10, 15)),
         "type": "deploy", "service": "payment-svc", "version": "v3.0.0",
         "changes": ["upgrade rust toolchain"], "actor": "ci-pipeline"},
        # I2 trap: numpy upgrade 24h before I2
        {"ts": iso(I2["start"] - timedelta(hours=24)),
         "type": "deploy", "service": "inventory-svc", "version": "v2.5.0",
         "changes": ["dependency bump: numpy 1.26 -> 2.0.0"],
         "actor": "ci-pipeline"},
        # I2 red herring: marketing flag mid-incident
        {"ts": iso(I2["start"] + timedelta(minutes=15)),
         "type": "config", "service": "frontend",
         "changes": ["feature flag homepage_promo_carousel set to enabled"],
         "actor": "marketing-team"},
        # I3 trap: RDS class change 24h before
        {"ts": iso(I3["start"] - timedelta(hours=24)),
         "type": "infra", "service": "rds-orders",
         "changes": ["instance class db.r6g.large -> db.r6g.xlarge"],
         "actor": "platform-team"},
        # I3 ROOT: feature flag enable_loyalty_recommendations
        {"ts": iso(I3["start"]),
         "type": "config", "service": "payment-svc",
         "changes": ["feature flag enable_loyalty_recommendations "
                     "set to enabled"],
         "actor": "product-team",
         "note": "rollout: 100% — tested in staging on dataset of "
                 "10k users"},
        # I4 trap: firewall change 48h prior
        {"ts": iso(I4["start"] - timedelta(hours=48)),
         "type": "infra", "service": "payment-svc",
         "changes": ["security group sg-pay-egress: removed legacy "
                     "rule 0.0.0.0/0:443"],
         "actor": "security-team"},
        # I4 ROOT context: DNS rotation announcement 7 days before
        {"ts": iso(I4["start"] - timedelta(days=7)),
         "type": "announcement", "service": "payment-svc",
         "changes": ["vendor notice: pp-api IP block rotating "
                     "203.0.113.0/24 -> 198.51.100.0/24, TTL 3600, "
                     "old block removed 2026-03-22 09:00 UTC"],
         "actor": "external"},
        # I4 trap: SSL cert rotation 5 days prior
        {"ts": iso(I4["start"] - timedelta(days=5)),
         "type": "cert", "service": "payment-svc",
         "changes": ["TLS cert rotated for pp-api client mTLS"],
         "actor": "platform-team"},
        # I5 trap: payment deploy 30 min before I5
        {"ts": iso(I5["start"] - timedelta(minutes=30)),
         "type": "deploy", "service": "payment-svc", "version": "v3.2.1",
         "changes": ["log format: switch to ECS-compatible "
                     "field naming"],
         "actor": "ci-pipeline"},
        # Non-incident: scheduled backup announced 8h before 03-14 window
        {"ts": iso(BACKUP["start"] - timedelta(hours=8)),
         "type": "maintenance", "service": "rds-orders",
         "changes": ["scheduled one-time full DB backup: window "
                     "2026-03-14 02:00-03:00 UTC; expect elevated "
                     "read replica lag and synthetic check noise"],
         "actor": "dba-team",
         "note": "approved in change board CR-2026-0287"},
        # I5 ROOT context: mesh cert rotation note
        {"ts": iso(I5["start"]),
         "type": "infra", "service": "internal-mesh-v1",
         "changes": ["automatic 24h cert rotation cycle: "
                     "checkout-svc -> payment-svc mTLS cert renewed, "
                     "not_before=2026-03-27T06:00:15Z"],
         "actor": "service-mesh-controller"},
    ]
    E.sort(key=lambda e: e["ts"])
    (ROOT / "deploy_log.json").write_text(json.dumps(E, indent=2) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# traces.json — sampled, with incident-flavored traces
# ────────────────────────────────────────────────────────────────────────────

def write_traces() -> None:
    T = []

    # Normal baseline traces, scattered
    for i in range(20):
        day = random.randint(1, 30)
        t = utc(2026, 3, day, random.randint(8, 20),
                random.randint(0, 59), random.randint(0, 59))
        T.append({"trace_id": trace_id(), "started_at": iso(t),
                  "duration_ms": random.randint(180, 240),
                  "status": "ok",
                  "spans": [
                      {"service": "frontend", "op": "POST /checkout",
                       "duration_ms": 220, "status": "ok"},
                      {"service": "checkout-svc", "op": "process",
                       "duration_ms": 180, "status": "ok"},
                      {"service": "payment-svc", "op": "charge",
                       "duration_ms": 120, "status": "ok"}]})

    # I1: fx 3-retry spans
    for i in range(8):
        t = I1["start"] + timedelta(seconds=120 + i * 100)
        T.append({"trace_id": trace_id(), "started_at": iso(t),
                  "duration_ms": random.randint(5500, 8500),
                  "status": "error",
                  "spans": [
                      {"service": "frontend", "op": "POST /checkout",
                       "duration_ms": 8200, "status": "error"},
                      {"service": "checkout-svc", "op": "process",
                       "duration_ms": 8000, "status": "error"},
                      {"service": "payment-svc", "op": "charge",
                       "duration_ms": 7800, "status": "error",
                       "error": "conn_pool_acquire_timeout"},
                      {"service": "payment-svc", "op": "fx_client.convert",
                       "duration_ms": 6500, "status": "error",
                       "error": "fx_503_after_3_retries",
                       "attempts": [
                           {"attempt": 1, "duration_ms": 1800,
                            "status": 503},
                           {"attempt": 2, "duration_ms": 2200,
                            "status": 503},
                           {"attempt": 3, "duration_ms": 2500,
                            "status": 503}]}]})

    # I2: inventory slow traces
    for i in range(6):
        t = I2["start"] + timedelta(minutes=60 + i * 20)
        T.append({"trace_id": trace_id(), "started_at": iso(t),
                  "duration_ms": random.randint(2000, 3500),
                  "status": "error",
                  "spans": [
                      {"service": "checkout-svc", "op": "process",
                       "duration_ms": 3200, "status": "error"},
                      {"service": "inventory-svc", "op": "stock_lookup",
                       "duration_ms": 2800, "status": "error",
                       "error": "gc_pause"}]})

    # I3: slow query in payment-svc.loyalty_client
    for i in range(6):
        t = I3["start"] + timedelta(minutes=5 + i * 7)
        T.append({"trace_id": trace_id(), "started_at": iso(t),
                  "duration_ms": random.randint(3500, 4500),
                  "status": "error",
                  "spans": [
                      {"service": "checkout-svc", "op": "process",
                       "duration_ms": 4100, "status": "error"},
                      {"service": "payment-svc", "op": "charge",
                       "duration_ms": 3900, "status": "error",
                       "error": "downstream_timeout"},
                      {"service": "payment-svc",
                       "op": "loyalty_client.recommend",
                       "duration_ms": 3700, "status": "error",
                       "error": "conn_pool_acquire_timeout",
                       "db_query": ("SELECT * FROM transactions "
                                    "WHERE user_id = ? ORDER BY ts DESC"),
                       "rows_examined": 180000,
                       "uses_index": False}]})

    # I4: bimodal — half succeed (az a/b), half fail (az c)
    for i in range(8):
        t = I4["start"] + timedelta(minutes=2 + i * 5)
        az = "c" if i % 2 == 0 else random.choice(["a", "b"])
        if az == "c":
            T.append({"trace_id": trace_id(), "started_at": iso(t),
                      "duration_ms": random.randint(60, 200),
                      "status": "error",
                      "spans": [
                          {"service": "checkout-svc", "op": "process",
                           "duration_ms": 120, "status": "error",
                           "az": "c"},
                          {"service": "payment-svc", "op": "charge",
                           "duration_ms": 80, "status": "error",
                           "az": "c",
                           "error": "connection_refused",
                           "remote_host": "pp.vendor.example",
                           "resolved_ip": "203.0.113.10"}]})
        else:
            T.append({"trace_id": trace_id(), "started_at": iso(t),
                      "duration_ms": random.randint(190, 230),
                      "status": "ok",
                      "spans": [
                          {"service": "checkout-svc", "op": "process",
                           "duration_ms": 200, "status": "ok",
                           "az": az},
                          {"service": "payment-svc", "op": "charge",
                           "duration_ms": 150, "status": "ok",
                           "az": az,
                           "remote_host": "pp.vendor.example",
                           "resolved_ip": "198.51.100.20"}]})

    # I5: TLS handshake errors with clock skew evidence
    for i in range(6):
        t = I5["start"] + timedelta(seconds=30 + i * 90)
        T.append({"trace_id": trace_id(), "started_at": iso(t),
                  "duration_ms": random.randint(400, 700),
                  "status": "error",
                  "spans": [
                      {"service": "checkout-svc", "op": "process",
                       "duration_ms": 500, "status": "error",
                       "error": "mtls_failure"},
                      {"service": "checkout-svc",
                       "op": "mtls_client.handshake",
                       "duration_ms": 200, "status": "error",
                       "error": "certificate_not_yet_valid",
                       "not_before": "2026-03-27T06:00:15Z",
                       "observed_at_on_validator":
                       "2026-03-27T05:59:48Z",
                       "validator_clock_skew_seconds": -27}]})

    T.sort(key=lambda r: r["started_at"])
    (ROOT / "traces.json").write_text(json.dumps(T, indent=2) + "\n")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    write_metrics()
    gen_frontend()
    gen_api_gateway()
    gen_checkout()
    gen_payment()
    gen_inventory()
    gen_rds()
    gen_redis()
    write_alerts()
    write_deploy_log()
    write_traces()
    print("data pack generated: 30 days, 5 incidents (I1..I5)")


if __name__ == "__main__":
    main()
