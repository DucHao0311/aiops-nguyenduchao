# Grading Rubric — Lab: Incident Forensics

Internal scoring guide. Not student-facing.

## Scoring model

Each of the 5 incidents is scored independently on 5 dimensions, each
graded 1–5. The per-incident score is the **average of the 5
dimensions**. A separate non-incident discrimination dimension (d6) is
scored once on the whole report. The final lab score weights the 5
incident scores at 90% and d6 at 10%.

```
incident_score(i) = mean(d1, d2, d3, d4, d5)
incidents_mean    = mean(incident_score(I-1..I-5))
final_score       = 0.9 * incidents_mean + 0.1 * d6
```

Range: 0.0 – 5.0.

## Adjustments

- **Missing incident** (no section written for one of the five): the
  incident's score is **0.0** across all dimensions.
- **Spurious incident** (a section claims an incident that is not
  actually in the data): subtract **0.5** from the final score per
  spurious section. Floor at 0.0.
- **Wrong incident window** (window claimed differs from truth by
  > 4 hours): treat as missing for that incident.
- **Right window, wrong root**: incident is scored normally; root-cause
  dimension is at most 2 (see d4 below).

## Per-dimension scale

Apply each rubric independently per incident.

### d1 — Timeline reconstruction

| Score | Criteria |
|---|---|
| 5 | All key events present (≥6); every entry has a timestamp **and** a citation to the source file or alert ID; ordering correct |
| 4 | Most key events present; 1–2 entries missing citations or ordering nit |
| 3 | Core events present (≥4); several entries lack citations |
| 2 | Partial timeline; events listed but mostly without sources |
| 1 | A few timestamps with no structure; cannot follow the incident from the timeline alone |

### d2 — Hypothesis enumeration

| Score | Criteria |
|---|---|
| 5 | ≥3 distinct hypotheses, each with non-trivial reasoning; covers the dominant trap(s) and the real cause |
| 4 | 3 hypotheses; one feels boilerplate but the others are substantive |
| 3 | 2–3 hypotheses; reasoning thin |
| 2 | 1–2 hypotheses; reasoning superficial |
| 1 | Single hypothesis stated as conclusion; no enumeration |

### d3 — False-lead dismissal

| Score | Criteria |
|---|---|
| 5 | Every non-root hypothesis explicitly dismissed with at least one piece of cited evidence (log line, metric value at timestamp, or alert ID) |
| 4 | All dismissed but one is weakly evidenced ("looks fine") |
| 3 | About half dismissed with evidence; rest implied |
| 2 | One dismissal with evidence; others ignored |
| 1 | No dismissals; non-root hypotheses left dangling |

### d4 — Root cause identification

| Score | Criteria |
|---|---|
| 5 | Correct root, named specifically (vendor / component / field); causal chain shown with mechanism (not just temporal order) |
| 4 | Correct root; causal chain partial or one link unsupported |
| 3 | Correct root; no mechanism shown — just "X caused the cascade" |
| 2 | Wrong root but plausibly in the chain (e.g., names an effect node) |
| 1 | Wrong root unrelated to the chain (e.g., named a pure trap as the cause) |

Refer to `RCA_REPORT.md` (this directory) for the canonical root of
each incident.

### d5 — Counterfactual reasoning

| Score | Criteria |
|---|---|
| 5 | For every dismissed hypothesis, a concrete reason "fixing X would not have resolved the user-visible symptom" with mechanism |
| 4 | All dismissed hypotheses have counterfactuals; 1 is weak ("doesn't help") |
| 3 | Half have counterfactuals |
| 2 | One counterfactual; rest missing |
| 1 | No counterfactual reasoning; section absent or generic |

### d6 — Non-incident discrimination (whole-report)

Scored once for the whole report based on the "Non-incident events
ruled out" section. There are two routine ops events in the data
pack: a weekly Thursday 14:00-14:30 ETL bumping RDS CPU to ~60% (no
alert), and a one-time 03-14 02:00-03:00 backup window (1 alert
fires, deploy_log entry exists 8 h prior).

| Score | Criteria |
|---|---|
| 5 | Both routine ops events identified and ruled out with cited evidence (deploy_log, completion log line, recurrence pattern) |
| 4 | Both identified; one ruled out weakly |
| 3 | One ops event identified; the other missed |
| 2 | Section present but evidence thin; OR one incident incorrectly classified as non-incident |
| 1 | Section absent; OR a real incident classified as routine |

**Auto-deduction**: each per-incident section written for a window
that is actually a non-incident (e.g., a section titled "I-6: 03-14
backup-induced latency") subtracts 0.5 from the final score AND caps
d6 at 2.

## Tie-breakers and notes for the grader

- **Pattern-matching across incidents**: if I-3's section reads as a
  copy of I-1's reasoning (retry storm narrative on a slow-query
  incident), d2/d3/d4 cap at 3 even if d1 and d5 are clean.
- **AZ awareness on I-4**: a report that does not mention `az`,
  `resolved_ip`, or the bimodal success/failure split caps at 3 on d4
  regardless of root-cause naming. The AZ split is the diagnostic
  feature, not an incidental detail.
- **Clock skew evidence on I-5**: explicit reference to the
  `not_before` vs `current_time` delta (or the `delta_seconds` field)
  is required for d4 = 5. "It was a cert issue" without the clock
  evidence caps at d4 = 3.
- **Memory leak vs. effect on I-2**: distinguishing the real leak
  (cache_entry_added with `ttl_seconds=null`) from the leak-shaped
  effect in I-1 (held buffers) is required for d3 = 5.
- **Correlator script**: if the submission's `correlator.py` is
  hard-coded (matches alert names by string rather than deriving
  groupings from the data), the d3 score across all incidents caps
  at 3 — the script is part of the evidence apparatus.

## Final-score tier mapping

| Final score | Tier | Interpretation |
|---|---|---|
| 4.5 – 5.0 | Excellent | All five roots correct, all traps dismissed with evidence, counterfactuals clean |
| 3.5 – 4.4 | Good | 4–5 roots correct, most traps dismissed; weaknesses on counterfactual or one incident |
| 2.5 – 3.4 | Adequate | 3 roots correct, hypothesis-enum present, few counterfactuals |
| 1.5 – 2.4 | Weak | 1–2 roots correct, mostly pattern-matched |
| < 1.5 | Fail | Missing sections or all guesses |

## Quick-reference answer key

| Incident | True root (one line) |
|---|---|
| I-1 | Third-party `fx-api` 503 burst + no circuit breaker on payment-svc → retry storm saturates outbound HTTP connection pool |
| I-2 | inventory-svc response_cache has no TTL on 5 MB SKU entries; nightly_sku_sync at 02:00 monotonically grows heap to OOM |
| I-3 | `enable_loyalty_recommendations` flag enabled at 11:15:00 → unindexed `SELECT * FROM transactions WHERE user_id=?` in payment-svc → RDS pool drained |
| I-4 | Vendor IP rotation for pp-api; AZ-c resolver served stale `203.0.113.10` past TTL; `connection_refused` for AZ-c only |
| I-5 | Service mesh cert rotation issued cert with `not_before=06:00:15Z`; validator NTP clock skew of −27 s → `certificate_not_yet_valid` until clocks reconverge |

| Incident | Strongest false positive (the one most students will fall for) |
|---|---|
| I-1 | RDS CPU 88% spike |
| I-2 | numpy 2.0 upgrade 24 h prior |
| I-3 | "Same pool-saturated alert as I-1, must be retry storm" |
| I-4 | "Firewall rule change 48 h ago" |
| I-5 | "Payment-svc deploy 30 min before" |
