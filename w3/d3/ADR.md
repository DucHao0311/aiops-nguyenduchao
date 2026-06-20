# ADR-008: Pre-Deploy Regex Complexity Gate for WAF / Pattern Rules

## Status
Accepted

---

## Context

The Cloudflare WAF outage (2019-07-02) and its reproduction in this lab (W3-D3, outage #3) demonstrated a class of failure that the AIOps runtime pipeline cannot prevent — only detect after damage has begun. The pipeline successfully detected the fault at MTTD = 4 seconds (Gap 1 in `rca_observed.json`), but the first 4 seconds of a regex CPU-pin incident on a global edge deployment are already catastrophic: all CPU is saturated before any alert fires, and all in-flight requests are already degraded.

This failure mode — **catastrophic backtracking** — is a property of the regular expression itself, deterministically reproducible, and statically analyzable. The root cause is not a runtime infrastructure fault but a **deploy-time validation omission**: the WAF rule deployment pipeline lacked a complexity gate that would reject O(n²+) regexes before they reached production.

The same pattern applies to any AIOps platform that ingests "pattern rules" from operators or automated systems:
- WAF rules (regex-based traffic filtering)
- Alert rules (Prometheus PromQL with expensive subqueries)
- Log parsing rules (Drain/Spell with complex regex templates)
- Anomaly detector feature expressions (complex lambda functions)

The decision required: **at what stage should regex/pattern complexity be validated, and what is the enforcement mechanism?**

Forces at play:
- Developer velocity: complexity checks add friction to the rule authoring workflow
- Safety: a single O(2^n) regex can saturate an entire service
- Observability: runtime detection catches the fault but cannot prevent the first wave of degradation
- Scope: complexity analysis tooling exists (Python `recheck` library, OWASP ReDoS checker, `safe-regex` npm) but is not integrated into CI pipelines by default

---

## Decision

**The AIOps rule deployment pipeline will enforce a mandatory pre-deploy regex complexity gate** implemented as a CI/CD step that statically analyzes every candidate regex/pattern rule before any deployment is approved. Rules that exhibit polynomial or exponential worst-case evaluation time (complexity class > O(n·log n)) are rejected with a blocking failure, preventing deployment to any production tier.

Implementation:
1. Every WAF rule, alert rule, and log parsing rule goes through `validate_rule_complexity(pattern: str) -> ComplexityReport` before promotion from `staging` to `production`
2. ComplexityReport includes: `worst_case_class` (linear | polynomial | exponential), `example_adversarial_input`, `max_evaluation_ms_at_n50`
3. Rules with `worst_case_class IN {polynomial, exponential}` are auto-rejected; engineer must simplify or receive explicit `SECURITY_OVERRIDE` approval from a second reviewer
4. Rules with `max_evaluation_ms_at_n50 > 10ms` trigger a warning but not a block (O(n log n) patterns with high constants still need review)
5. The gate runs in < 5 seconds (local execution, no network dependency) to preserve developer velocity

---

## Alternatives Considered

### Alternative 1 — Runtime Detection Only (status quo)

The existing AIOps pipeline detects CPU saturation and HighLatency alerts at MTTD = 4 seconds. On CPU saturation, the runtime RCA fires and an operator rolls back the rule.

**Pros:**
- Zero additional developer friction — no pre-deploy step
- Already implemented and working in current pipeline (MTTD = 4s in lab)
- Handles all failure modes, not just regex — catches CPU spikes from any source

**Cons:**
- 4 seconds of full CPU saturation means every in-flight request during those 4 seconds is degraded or lost; at Cloudflare scale (7M req/s), that is 28 million failed requests before the first alert fires
- Runtime detection requires the fault to manifest in production; a pre-deploy gate prevents it entirely
- Rollback requires operator action under stress; pre-deploy rejection is zero-touch
- The gap identified in `rca_observed.json` Gap 2: "Pipeline was entirely reactive — it fired after the first request was already degraded." The architecture assumes runtime recoverability; this class of fault requires prevention at deploy time.

**Rejected:** Runtime detection alone is insufficient for instantaneous CPU-saturation failures. Necessary as a backstop but not sufficient as the primary defense.

---

### Alternative 2 — Canary Rollout Gate (1% → 10% → 100% with CPU monitor)

Deploy WAF rules to 1% of production nodes first; monitor CPU for 60 seconds; abort if CPU increases > 5%; proceed to 10% if clean.

**Pros:**
- Limits blast radius dramatically — 1% canary means only 1% of users impacted during the fault window
- Catches non-regex failure modes that static analysis cannot predict (new dependency, unexpected input distribution)
- Already a best-practice for infrastructure changes (matches Cloudflare's own post-incident recommendation)
- Complementary to the static gate — canary catches what static analysis misses

**Cons:**
- Canary rollout adds 3–5 minutes to every WAF rule deployment (monitoring window per stage)
- Canary assumes the 1% cohort is representative — edge cases with specific adversarial inputs might not appear in the canary window
- Does not prevent the fault for the 1% canary cohort — they still experience the degradation
- Adds operational complexity: canary infrastructure, automated gate logic, stage promotion rules

**Rejected as sole solution:** Canary alone still allows a window of user impact on the canary slice. Retained as a complementary control — the decision implements both the static gate (primary) and recommends canary rollout (secondary) in the Consequences section.

---

### Alternative 3 — Sandbox Execution with Timeout

Run every candidate WAF rule in an isolated sandbox container with a strict CPU timeout (e.g., 500ms); test it against a synthetic adversarial corpus; if the rule exceeds the timeout on any corpus entry, reject it.

**Pros:**
- Dynamic rather than static — catches backtracking that static analysis might miss (complex regex combinations)
- Language-agnostic — works for any pattern language, not just regex
- Can test with realistic input distributions

**Cons:**
- Adversarial corpus quality matters — if the corpus does not include the specific backtracking trigger, the sandbox passes a malicious rule
- Container spin-up adds 10–30 seconds per rule (slower than static analysis at < 5s)
- False negatives possible if adversarial input is not in corpus — gives false confidence
- Sandbox escape risk if the rule language is Turing-complete (not applicable for pure regex, but relevant for extended rule languages)

**Rejected as primary gate:** The adversarial corpus completeness problem makes this unreliable as the primary gate. Retained as a complementary test in staging — the decision recommends sandbox testing as a supplemental step after the static complexity check.

---

## Consequences

**Positive:**
- Prevents the entire class of regex CPU-saturation outages at deploy time — zero production impact rather than 4-second degradation window
- Provides actionable feedback to rule authors: rejected rules include `example_adversarial_input` and `worst_case_class`, allowing targeted simplification
- Closes Gap 2 from `rca_observed.json`: "No amount of faster MTTD in the runtime pipeline fixes a global deploy that takes < 5 seconds to saturate all nodes"
- Integrates into existing CI/CD without requiring a new infrastructure component — pure Python static analysis (`recheck` library, BSD-3 license)
- Audit trail: all complexity gate decisions (pass/reject/override) are logged with rule content and analysis result — supports postmortem investigation

**Negative (trade-offs accepted):**
- Adds 3–8 seconds to the WAF rule promotion CI step — accepted trade-off for preventing catastrophic outages
- Static complexity analysis has false negatives for dynamically constructed regexes (concatenated patterns) — these pass static analysis but may still backtrack; mitigated by the canary rollout recommendation
- Developers unfamiliar with NFA-based regex engines may not understand rejection messages — requires a short onboarding document explaining catastrophic backtracking and safe alternative patterns

**Risks introduced:**
- Over-broad rejection: an overly conservative complexity threshold may reject legitimate rules that are fast in practice (e.g., a rule that is technically O(n²) but only for inputs > 10,000 characters, unlikely in production). Mitigation: tune the `max_evaluation_ms_at_n50` threshold to 10ms based on observed production input lengths; allow `SECURITY_OVERRIDE` for reviewed exceptions.
- False sense of security: the gate only catches regex complexity. CPU saturation from other sources (algorithmic complexity in application code, unbounded iteration) is not caught by this gate. The runtime pipeline (existing) remains the backstop for non-regex CPU faults.

**Reference to observed gap (§9.4):**  
This ADR directly addresses Gap 2 from `rca_observed.json`: the pipeline was entirely reactive and fired after production degradation had already begun. The pre-deploy gate shifts the defense left, from runtime detection (MTTD = 4s into the outage) to deploy-time prevention (MTTD = 0, fault never reaches production).
