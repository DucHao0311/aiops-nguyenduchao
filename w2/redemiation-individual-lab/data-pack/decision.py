"""
Layer 3: Action Selection
Combines retrieval confidence with cost/blast-radius metadata to pick a final action.

Decision model: Expected-Value with asymmetric loss
  EV(action) = P_success * benefit - (1 - P_success) * blast_cost
  benefit = 1.0 (fixed: incident resolved)
  blast_cost = blast_radius_services * downtime_min * BLAST_PENALTY

page_oncall has zero cost in the catalog, which would make it trivially best under naive EV.
We fix this by assigning page_oncall a benefit of ESCALATION_BENEFIT < 1.0 — it resolves
the incident eventually but more slowly, so its EV is capped.

Gate rules:
  - If OOD → always page_oncall (no auto-action)
  - If best candidate blast_radius_services >= HIGH_BLAST and confidence < HIGH_BLAST_CONF_GATE
    → page_oncall (too risky to auto-act at low confidence)
  - If consensus_score < MIN_CONFIDENCE → page_oncall (not enough signal)
  - Otherwise: pick highest EV action
"""
from __future__ import annotations

# ── Tuning constants ──────────────────────────────────────────────────────────
BLAST_PENALTY = 0.15        # per blast-radius-service × downtime multiplier (lowered from 0.3)
HIGH_BLAST = 3              # blast_radius_services threshold for extra caution
HIGH_BLAST_CONF_GATE = 0.55 # minimum confidence to auto-act on high-blast actions
MIN_CONFIDENCE = 0.15       # below this → escalate regardless
ESCALATION_BENEFIT = 0.72   # reduced benefit for page_oncall (slow resolution, raised from 0.6→0.7→0.72)


def _get_action_meta(name: str, catalog: list[dict]) -> dict:
    """Look up action metadata from the actions catalog."""
    for a in catalog:
        if a['name'] == name:
            return a
    return {
        "name": name,
        "cost_min": 0,
        "downtime_min": 0,
        "blast_radius_services": 0,
        "rollback_window_sec": 0,
    }


def _build_params(action_name: str, candidate: dict, catalog: list[dict], affected_services: list[str]) -> dict:
    """
    Construct the params dict for the selected action.

    Rules:
    - rollback_service  → {service: <best_service or trigger>, target_version: "previous"}
    - increase_pool_size → {service: <best_service>, from_value: "current", to_value: "increased"}
    - restart_pod       → {service: <best_service>, pod_selector: "all"}
    - dns_config_rollback → {configmap_name: "dns-config", target_revision: "previous"}
    - network_policy_revert → {policy_name: "default"}
    - page_oncall       → {team: "platform-team"}
    """
    svc = candidate.get("best_service") or (affected_services[0] if affected_services else "unknown")

    if action_name == "rollback_service":
        return {"service": svc, "target_version": "previous"}
    elif action_name == "increase_pool_size":
        return {"service": svc, "from_value": "current", "to_value": "increased"}
    elif action_name == "restart_pod":
        return {"service": svc, "pod_selector": "all"}
    elif action_name == "dns_config_rollback":
        return {"configmap_name": "dns-config", "target_revision": "previous"}
    elif action_name == "network_policy_revert":
        return {"policy_name": "default"}
    elif action_name == "page_oncall":
        return {"team": "platform-team"}
    else:
        # fallback: use positional params from history if available
        raw = candidate.get("raw_params", [])
        return {"params": raw}


def compute_ev(action_name: str, candidate_score: float, action_meta: dict) -> float:
    """
    Compute Expected Value for an action.

    P_success ≈ candidate_score (our retrieval confidence)
    benefit = 1.0 for all real actions; ESCALATION_BENEFIT for page_oncall
    blast_cost = blast_radius_services × downtime_min × BLAST_PENALTY
    """
    p = candidate_score
    blast = action_meta.get('blast_radius_services', 0)
    downtime = action_meta.get('downtime_min', 0)

    if action_name == "page_oncall":
        benefit = ESCALATION_BENEFIT
        blast_cost = 0.0
    else:
        benefit = 1.0
        blast_cost = blast * downtime * BLAST_PENALTY

    ev = p * benefit - (1 - p) * blast_cost
    return round(ev, 4)


def select_action(retrieval_result: dict, actions_catalog: list[dict], affected_services: list[str], trigger_rule: str = "", log_templates: list = None, trigger_service: str = "") -> dict:
    """
    Layer 3 entry point.

    Special case: if trigger_rule contains 'memory_leak' or 'memory' and logs show
    OOM/GC errors, bypass retrieval and recommend restart_pod for the trigger service.

    Returns a decision dict:
    {
        "selected_action": str,
        "params": dict,
        "confidence": float,
        "ev": float,
        "blast_radius_check": str,
        "decision_reason": str,
        "candidates_evaluated": [...],
        "escalation": bool,
    }
    """
    # ── Special case: Memory leak detection ────────────────────────────────────
    log_templates = log_templates or []
    if trigger_rule and any(kw in trigger_rule.lower() for kw in ['memory', 'heap', 'gc']):
        if any('memory' in t.lower() or 'oom' in t.lower() or 'gc' in t.lower() for t in log_templates[:5]):
            # For memory leak, use trigger_alert.service (where alert fired)
            svc = trigger_service if trigger_service else (affected_services[0] if affected_services else "unknown")
            meta = _get_action_meta("restart_pod", actions_catalog)
            return {
                "selected_action": "restart_pod",
                "params": {"service": svc, "pod_selector": "all"},
                "confidence": 0.75,
                "ev": compute_ev("restart_pod", 0.75, meta),
                "blast_radius_check": "OK",
                "selected_action_meta": {
                    "cost_min": meta.get("cost_min"),
                    "downtime_min": meta.get("downtime_min"),
                    "blast_radius_services": meta.get("blast_radius_services"),
                },
                "decision_reason": f"Memory leak pattern detected (rule={trigger_rule}, service={svc}); recommending restart_pod",
                "candidates_evaluated": [],
                "escalation": False,
            }

    # ── Gate 1: OOD ──────────────────────────────────────────────────────────
    if retrieval_result.get("ood", False):
        reason = retrieval_result.get("ood_reason", "OOD: no close historical match")
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 0.0,
            "ev": 0.0,
            "blast_radius_check": "N/A (OOD)",
            "decision_reason": f"OOD escalation — {reason}",
            "candidates_evaluated": [],
            "escalation": True,
        }

    candidates = retrieval_result.get("candidates", {})
    consensus = retrieval_result.get("consensus_score", 0.0)

    # ── Gate 2: consensus too low ─────────────────────────────────────────────
    if not candidates or consensus < MIN_CONFIDENCE:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": consensus,
            "ev": 0.0,
            "blast_radius_check": "N/A (low confidence)",
            "decision_reason": f"Confidence {consensus:.3f} < threshold {MIN_CONFIDENCE} — escalating",
            "candidates_evaluated": [],
            "escalation": True,
        }

    # ── Evaluate all candidates ───────────────────────────────────────────────
    evaluated = []
    for name, cand in candidates.items():
        meta = _get_action_meta(name, actions_catalog)
        ev = compute_ev(name, cand["score"], meta)
        evaluated.append({
            "action": name,
            "score": cand["score"],
            "ev": ev,
            "blast_radius": meta.get("blast_radius_services", 0),
            "downtime_min": meta.get("downtime_min", 0),
            "vote_count": cand.get("vote_count", 0),
            "meta": meta,
            "candidate": cand,
        })

    # Sort by EV descending
    evaluated.sort(key=lambda x: x["ev"], reverse=True)

    # ── Gate 3: blast-radius check on top candidate ───────────────────────────
    best = evaluated[0]
    blast_check = "OK"

    if best["action"] != "page_oncall":
        if best["blast_radius"] >= HIGH_BLAST and best["score"] < HIGH_BLAST_CONF_GATE:
            blast_check = (
                f"BLOCKED (blast_radius={best['blast_radius']} >= {HIGH_BLAST}, "
                f"confidence={best['score']:.2f} < {HIGH_BLAST_CONF_GATE})"
            )
            # Demote to page_oncall
            meta_oc = _get_action_meta("page_oncall", actions_catalog)
            oc_cand = candidates.get("page_oncall", {"score": consensus, "best_service": None, "raw_params": [], "vote_count": 0})
            oc_ev = compute_ev("page_oncall", oc_cand.get("score", consensus), meta_oc)

            return {
                "selected_action": "page_oncall",
                "params": {"team": "platform-team"},
                "confidence": round(consensus, 4),
                "ev": oc_ev,
                "blast_radius_check": blast_check,
                "decision_reason": (
                    f"Top candidate '{best['action']}' blocked by blast-radius gate. "
                    f"Escalating to page_oncall."
                ),
                "candidates_evaluated": _summarise_evaluated(evaluated),
                "escalation": True,
            }

    # ── Final selection ───────────────────────────────────────────────────────
    winner = best
    meta = winner["meta"]
    cand = winner["candidate"]
    params = _build_params(winner["action"], cand, actions_catalog, affected_services)

    return {
        "selected_action": winner["action"],
        "params": params,
        "confidence": round(winner["score"], 4),
        "ev": winner["ev"],
        "blast_radius_check": blast_check,
        "selected_action_meta": {
            "cost_min": meta.get("cost_min"),
            "downtime_min": meta.get("downtime_min"),
            "blast_radius_services": meta.get("blast_radius_services"),
        },
        "decision_reason": (
            f"Highest EV={winner['ev']:.3f} with confidence={winner['score']:.3f}; "
            f"blast_radius={winner['blast_radius']}; downtime={winner['downtime_min']}min"
        ),
        "candidates_evaluated": _summarise_evaluated(evaluated),
        "escalation": winner["action"] == "page_oncall",
    }


def _summarise_evaluated(evaluated: list[dict]) -> list[dict]:
    return [
        {
            "action": e["action"],
            "score": e["score"],
            "ev": e["ev"],
            "blast_radius": e["blast_radius"],
            "vote_count": e["vote_count"],
        }
        for e in evaluated
    ]
