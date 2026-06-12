"""
Layer 1: Feature Extraction
Converts raw incident evidence (logs, traces, metrics) into a comparable feature vector.

Key design decisions:
- Log features: template clustering via simple regex-normalization (lightweight Drain-inspired)
- Trace features: per-edge error_rate and p99_deviation_ratio from baseline
- Metric features: used ONLY as supplementary signal (not primary), as mandated
- Affected services: derived from trigger_alert + high-error traces + burst log activity
"""
from __future__ import annotations
import re
from collections import Counter, defaultdict
from typing import Optional


# ── Simple log template normalizer (lightweight Drain substitute) ──────────────
_NUMBER_RE = re.compile(r'\b\d+(\.\d+)?\b')
_HEX_RE = re.compile(r'\b[0-9a-fA-F]{6,}\b')
_IP_RE = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_UUID_RE = re.compile(r'\b[0-9a-fA-F\-]{32,}\b')
_PATH_RE = re.compile(r'/[\w/\.\-]+')
_VERSION_RE = re.compile(r'v\d+[\.\d]*')


def normalize_log_line(msg: str) -> str:
    """Convert a raw log line to a stable template by masking variable tokens."""
    t = msg
    t = _UUID_RE.sub('<UUID>', t)
    t = _IP_RE.sub('<IP>', t)
    t = _VERSION_RE.sub('<VER>', t)
    t = _HEX_RE.sub('<HEX>', t)
    t = _NUMBER_RE.sub('<NUM>', t)
    t = _PATH_RE.sub('<PATH>', t)
    # collapse repeated spaces
    t = re.sub(r'\s+', ' ', t).strip()
    # lower-case and truncate to first 80 chars for stability
    return t[:80].lower()


def extract_log_templates(logs: list[dict]) -> dict:
    """
    Returns:
        {
          "templates": list of unique normalized templates (sorted by frequency),
          "service_error_counts": {service: error_count},
          "top_services": [svc1, svc2, ...],  # services with most ERROR logs
        }

    Deduplication: we bucket templates by their first 4 words to avoid
    near-duplicate high-frequency templates (e.g., "upstream svc slow latency=X")
    swamping out informative rare ones.
    """
    template_counter: Counter = Counter()
    svc_errors: Counter = Counter()
    # Bucket by first-4-words prefix to deduplicate repetitive patterns
    prefix_seen: dict = {}

    for log in logs:
        tmpl = normalize_log_line(log.get('msg', ''))
        template_counter[tmpl] += 1
        if log.get('level', '').upper() in ('ERROR', 'CRITICAL', 'FATAL', 'WARN', 'WARNING'):
            svc_errors[log.get('svc', 'unknown')] += 1

    # Build a deduplicated set: keep top-1 template per 4-word prefix
    prefix_best: dict = {}  # prefix -> (count, template)
    for tmpl, count in template_counter.items():
        words = tmpl.split()[:4]
        prefix = ' '.join(words)
        if prefix not in prefix_best or count > prefix_best[prefix][0]:
            prefix_best[prefix] = (count, tmpl)

    # From deduped set, take top-20 by count
    deduped = sorted(prefix_best.values(), key=lambda x: x[0], reverse=True)
    top_templates = [t for _, t in deduped[:20]]

    top_services = [s for s, _ in svc_errors.most_common()]

    return {
        "templates": top_templates,
        "service_error_counts": dict(svc_errors),
        "top_services": top_services,
    }


def extract_trace_features(traces: list[dict]) -> dict:
    """
    Aggregate trace records per (from, to) edge.
    Returns per-edge: total_count, total_errors, error_rate, max_p99_ms, avg_p99_ms.
    Also returns the most anomalous edges (highest error_rate or p99).
    """
    edge_stats: dict = defaultdict(lambda: {
        "count": 0, "errors": 0, "p99_sum": 0.0, "p99_max": 0.0, "records": 0
    })

    for tr in traces:
        key = (tr.get('from', ''), tr.get('to', ''))
        s = edge_stats[key]
        s["count"] += tr.get('count', 0)
        s["errors"] += tr.get('error_count', 0)
        p99 = tr.get('p99_ms', 0.0)
        s["p99_sum"] += p99
        s["p99_max"] = max(s["p99_max"], p99)
        s["records"] += 1

    results = {}
    for (frm, to), s in edge_stats.items():
        total = s["count"]
        err_rate = s["errors"] / total if total > 0 else 0.0
        avg_p99 = s["p99_sum"] / s["records"] if s["records"] > 0 else 0.0
        results[f"{frm}->{to}"] = {
            "error_rate": round(err_rate, 4),
            "avg_p99_ms": round(avg_p99, 1),
            "max_p99_ms": round(s["p99_max"], 1),
        }

    # Identify top anomalous edges
    anomalous = sorted(
        results.items(),
        key=lambda kv: kv[1]["error_rate"] * 2 + kv[1]["avg_p99_ms"] / 1000,
        reverse=True
    )
    top_anomalous_edges = [k for k, _ in anomalous[:5]]

    return {
        "edge_stats": results,
        "top_anomalous_edges": top_anomalous_edges,
    }


def extract_metric_features(metrics_window: dict) -> dict:
    """
    Supplementary metric signal: detect anomalous services from metric series.
    Uses last-third vs first-third ratio to find degrading metrics.
    """
    anomalous_services = set()
    metric_deltas = {}

    samples = metrics_window.get('samples', {})
    for key, series in samples.items():
        if not series or len(series) < 6:
            continue
        values = [v for _, v in series]
        n = len(values)
        baseline = values[:n // 3]
        recent = values[2 * n // 3:]
        baseline_mean = sum(baseline) / len(baseline) if baseline else 0
        recent_mean = sum(recent) / len(recent) if recent else 0

        if baseline_mean > 0:
            ratio = recent_mean / baseline_mean
            if ratio > 1.5:
                svc = key.split('.')[0]
                anomalous_services.add(svc)
                metric_deltas[key] = round(ratio, 2)

    return {
        "anomalous_services": list(anomalous_services),
        "metric_ratios": metric_deltas,
    }


def find_primary_trace_culprit(trace_features: dict) -> str | None:
    """
    Return the service that is most likely the root cause based on trace anomaly.
    Uses the source service of the highest-error-rate edge as the culprit.
    A high-error-rate edge (>= 0.10) is a strong signal.
    """
    best_edge = None
    best_rate = 0.1  # minimum threshold to consider
    for edge_key, stats in trace_features.get('edge_stats', {}).items():
        if stats['error_rate'] > best_rate:
            best_rate = stats['error_rate']
            best_edge = edge_key
    if best_edge:
        # The source node of the highest-error-rate edge is the culprit service
        parts = best_edge.split('->')
        if len(parts) == 2:
            return parts[0]  # the service that is failing, not the downstream
    return None


def derive_affected_services(incident: dict, trace_features: dict, log_features: dict) -> list[str]:
    """
    Derive affected_services from the live evidence.

    Rule (documented in FINDINGS.md §1):
    1. The trigger_alert service is always affected.
    2. Any service appearing as src or dst on a trace edge with error_rate > 0.05
       or avg_p99_ms > 500 is considered affected.
    3. Any service with > 3 ERROR log lines is considered affected.
    4. The trace-primary culprit (highest error-rate edge source) is added first
       when its error_rate > 0.10, ensuring it ranks first in the list.
    """
    affected = set()

    # Rule 1: trigger service
    trigger_svc = incident.get('trigger_alert', {}).get('service', '')
    if trigger_svc:
        affected.add(trigger_svc)

    # Rule 2: high-error or high-latency trace edges
    for edge_key, stats in trace_features.get('edge_stats', {}).items():
        if stats['error_rate'] > 0.05 or stats['avg_p99_ms'] > 500:
            parts = edge_key.split('->')
            if len(parts) == 2:
                affected.update(parts)

    # Rule 3: services with burst error logs
    for svc, count in log_features.get('service_error_counts', {}).items():
        if count > 3:
            affected.add(svc)

    result = sorted(affected)

    # Rule 4: Put trace-primary culprit first if high confidence
    primary = find_primary_trace_culprit(trace_features)
    if primary and primary in result:
        result.remove(primary)
        result.insert(0, primary)

    return result


def extract_features(incident: dict) -> dict:
    """
    Layer 1 entry point.
    Returns an incident_vector dict suitable for similarity comparison.
    """
    log_feats = extract_log_templates(incident.get('logs', []))
    trace_feats = extract_trace_features(incident.get('traces', []))
    metric_feats = extract_metric_features(incident.get('metrics_window', {}))
    affected_svcs = derive_affected_services(incident, trace_feats, log_feats)

    trace_culprit = find_primary_trace_culprit(trace_feats)

    return {
        "incident_id": incident.get('incident_id', ''),
        "trigger_service": incident.get('trigger_alert', {}).get('service', ''),
        "trigger_rule": incident.get('trigger_alert', {}).get('rule_id', ''),
        "severity": incident.get('trigger_alert', {}).get('severity', ''),

        # Log signals
        "log_templates": log_feats["templates"],
        "log_error_services": log_feats["top_services"],
        "log_service_error_counts": log_feats["service_error_counts"],

        # Trace signals
        "trace_edge_stats": trace_feats["edge_stats"],
        "top_anomalous_edges": trace_feats["top_anomalous_edges"],
        "trace_primary_culprit": trace_culprit,  # service at root of highest-error-rate edge

        # Metric signals (supplementary)
        "metric_anomalous_services": metric_feats["anomalous_services"],
        "metric_ratios": metric_feats["metric_ratios"],

        # Derived
        "affected_services": affected_svcs,
    }
