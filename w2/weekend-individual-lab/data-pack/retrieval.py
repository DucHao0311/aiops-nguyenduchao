"""
Layer 2: Historical Retrieval + Outcome-Weighted Voting
Finds the k most similar historical incidents and derives a ranked candidate action list.

Similarity function: weighted hybrid of
  (a) Log template overlap (Jaccard on template sets)
  (b) Affected-service overlap (Jaccard on service sets)
  (c) Trace-edge signal match (Jaccard on top-anomalous-edge sets)

These three are cheap, interpretable, and don't overfit on a ~30-item corpus.

Alternative considered: TF-IDF cosine on log templates → requires at least hundreds
of documents to produce stable IDF weights; on 30 incidents it overfits.

Outcome weighting: success=1.0, partial=0.5, failed=0.0
This ensures failed historical actions are never promoted to top rank.

OOD detection: if the best similarity score < OOD_THRESHOLD the engine escalates.
"""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Optional

from optional_helpers import parse_history_action


# ── Constants ─────────────────────────────────────────────────────────────────
OOD_THRESHOLD = 0.10   # min similarity to trust a neighbour (lowered from 0.12)
TOP_K = 5              # neighbours to consider

OUTCOME_WEIGHTS = {
    "success": 1.0,
    "partial": 0.5,
    "failed": 0.0,
}

# Similarity component weights
W_LOG = 0.35       # log template overlap
W_SVC = 0.35       # affected-service overlap
W_TRACE = 0.30     # trace anomaly-edge overlap (increased)


# ── History index helpers ─────────────────────────────────────────────────────

def _parse_action_list(actions_taken: list[str]) -> list[dict]:
    """Parse raw history action strings into structured dicts."""
    parsed = []
    for s in actions_taken:
        a = parse_history_action(s)
        parsed.append(a)
    return parsed


def _history_log_template_set(entry: dict) -> set:
    """Normalise historical log_signatures into a comparable template set."""
    out = set()
    for sig in entry.get('log_signatures', []):
        # lower + strip numbers for lightweight normalisation
        t = re.sub(r'\d+', '<NUM>', sig.lower()).strip()
        out.add(t[:80])
    return out


def _history_service_set(entry: dict) -> set:
    return set(entry.get('affected_services', []))


def _history_trace_edge_set(entry: dict) -> set:
    """Build a set of 'from->to' strings from historical trace_signatures."""
    return {
        f"{ts['from']}->{ts['to']}"
        for ts in entry.get('trace_signatures', [])
    }


def _live_log_template_set(vec: dict) -> set:
    """Use the top-20 normalised templates from the live incident vector."""
    return set(vec.get('log_templates', []))


def _live_service_set(vec: dict) -> set:
    return set(vec.get('affected_services', []))


def _live_trace_edge_set(vec: dict) -> set:
    return set(vec.get('top_anomalous_edges', []))


# ── Similarity ────────────────────────────────────────────────────────────────

def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _fuzzy_log_overlap(live_set: set, hist_set: set) -> float:
    """
    Template matching that handles length mismatch between raw (longer) live templates
    and cleaned (shorter) historical signatures.

    Approach: normalize both to first-30-char + digit-masked prefix for matching.
    Returns a [0, 1] score comparable to Jaccard.
    """
    if not live_set and not hist_set:
        return 0.0

    _num = re.compile(r'\d+(\.\d+)?')

    def short_key(t: str) -> str:
        # Mask numbers, take first 30 chars
        return _num.sub('<N>', t)[:30]

    live_keys = {short_key(t) for t in live_set}
    hist_keys = {short_key(t) for t in hist_set}

    intersect = len(live_keys & hist_keys)
    # For Jaccard denominator, use the original set sizes (not keys)
    union_size = len(live_set | hist_set)

    return intersect / union_size if union_size > 0 else 0.0


def similarity(live_vec: dict, hist_entry: dict) -> float:
    """
    Weighted hybrid Jaccard similarity between a live incident vector
    and a historical corpus entry.

    When there's a dominant trace culprit in the live incident (error_rate > 0.10),
    we bias the service similarity toward that culprit's presence in the history.
    This prevents log-noise from a false service from dominating.
    """
    log_sim = _fuzzy_log_overlap(
        _live_log_template_set(live_vec),
        _history_log_template_set(hist_entry)
    )
    trace_sim = _jaccard(
        _live_trace_edge_set(live_vec),
        _history_trace_edge_set(hist_entry)
    )

    # Service similarity: use affected services by default, but if there's a
    # clear trace culprit, boost similarity to histories involving that service
    live_svcs = _live_service_set(live_vec)
    hist_svcs = _history_service_set(hist_entry)

    trace_culprit = live_vec.get('trace_primary_culprit')
    if trace_culprit:
        # Build a culprit-centric service set for comparison
        # Give strong weight if the history's affected services include the culprit
        if trace_culprit in hist_svcs:
            culprit_boost = 0.5  # Increased from 0.3 to 0.5
        else:
            culprit_boost = 0.0
        svc_sim = _jaccard(live_svcs, hist_svcs) * 0.5 + culprit_boost  # Reduced base weight from 0.7
    else:
        svc_sim = _jaccard(live_svcs, hist_svcs)

    return round(W_LOG * log_sim + W_SVC * svc_sim + W_TRACE * trace_sim, 4)


# ── OOD detection ─────────────────────────────────────────────────────────────

def is_ood(top_similarity: float) -> bool:
    """Return True when the best match is too weak to trust."""
    return top_similarity < OOD_THRESHOLD


# ── Retrieval + voting ────────────────────────────────────────────────────────

def retrieve_and_vote(live_vec: dict, history: list[dict], top_k: int = TOP_K) -> dict:
    """
    1. Score every historical incident against the live vector.
    2. Take top-k neighbours.
    3. Vote on actions, weighted by (similarity × outcome_weight).
    4. Return ranked candidates + diagnostics for Layer 3.

    Returns:
    {
        "ood": bool,
        "best_similarity": float,
        "top_neighbors": [...],          # for audit log
        "candidates": {                  # action_name -> {score, params, count}
            "rollback_service": {"score": 0.72, "params": [...], "count": 2},
            ...
        },
        "consensus_score": float,        # max candidate score (0..1)
    }
    """
    scored = []
    for entry in history:
        sim = similarity(live_vec, entry)
        scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    neighbours = scored[:top_k]

    best_sim = neighbours[0][0] if neighbours else 0.0

    # Build audit-friendly neighbour summary
    top_neighbors = []
    for sim, entry in neighbours:
        top_neighbors.append({
            "id": entry["id"],
            "root_cause_class": entry.get("root_cause_class", ""),
            "similarity": sim,
            "outcome": entry.get("outcome", "unknown"),
            "actions_taken": entry.get("actions_taken", []),
        })

    # OOD check
    if is_ood(best_sim):
        return {
            "ood": True,
            "best_similarity": best_sim,
            "top_neighbors": top_neighbors,
            "candidates": {},
            "consensus_score": 0.0,
            "ood_reason": f"Best similarity {best_sim:.3f} < threshold {OOD_THRESHOLD}",
        }

    # Outcome-weighted vote aggregation
    # vote[action_name] accumulates (sim * outcome_weight)
    vote_scores: dict = defaultdict(float)
    vote_counts: dict = defaultdict(int)
    vote_params: dict = {}   # action_name -> best params seen (from highest-scoring neighbour)
    vote_service: dict = defaultdict(lambda: defaultdict(float))  # action_name -> service -> score

    trace_culprit = live_vec.get('trace_primary_culprit')  # Get trace culprit for bias

    for sim, entry in neighbours:
        o_weight = OUTCOME_WEIGHTS.get(entry.get("outcome", "failed"), 0.0)
        if o_weight == 0.0:
            continue  # skip failed-outcome neighbours entirely in voting

        actions = _parse_action_list(entry.get("actions_taken", []))
        for action in actions:
            name = action["name"]
            raw_params = action.get("params", [])

            # Boost vote if action targets the trace culprit service
            vote_boost = 1.0
            if trace_culprit and raw_params and raw_params[0] == trace_culprit:
                vote_boost = 1.3  # 30% boost for culprit-targeting actions (reduced from 1.5)

            vote_scores[name] += sim * o_weight * vote_boost
            vote_counts[name] += 1

            # Determine the primary service parameter
            primary_service = raw_params[0] if raw_params else None

            if primary_service:
                vote_service[name][primary_service] += sim * o_weight * vote_boost

            # Keep params from the best-scoring neighbour for this action
            if name not in vote_params or sim > vote_params.get(f"_sim_{name}", 0):
                vote_params[name] = raw_params
                vote_params[f"_sim_{name}"] = sim

    # Normalise scores to 0..1 range (divide by sum of top-k sim*weight)
    total_weight = sum(
        sim * OUTCOME_WEIGHTS.get(e.get("outcome", "failed"), 0.0)
        for sim, e in neighbours
    )

    candidates = {}
    for name, score in vote_scores.items():
        normalised = score / total_weight if total_weight > 0 else 0.0
        raw_params = vote_params.get(name, [])

        # Resolve best service param by vote if there are multiple candidates
        best_service = None
        if name in vote_service and vote_service[name]:
            best_service = max(vote_service[name], key=vote_service[name].get)

        candidates[name] = {
            "score": round(normalised, 4),
            "raw_params": raw_params,
            "best_service": best_service,
            "vote_count": vote_counts[name],
        }

    consensus_score = max((v["score"] for v in candidates.values()), default=0.0)

    return {
        "ood": False,
        "best_similarity": best_sim,
        "top_neighbors": top_neighbors,
        "candidates": candidates,
        "consensus_score": round(consensus_score, 4),
    }
