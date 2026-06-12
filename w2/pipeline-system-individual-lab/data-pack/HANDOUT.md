# Lab — Observability + AIOps Stack Redesign

**Individual.**

This lab is design work. You produce architecture diagrams, decision records, cost models and a migration plan — not algorithms. There is no test set you maximise against. The deliverable is what an architect hands to a delivery team.

---

## 1. The brief

You are joining a platform team that operates a 10-service application. The current observability bill is **$42,000 / month**, split across three SaaS vendors plus a handful of supporting tools. Despite the spend, incidents still take 25–40 minutes to diagnose because the data is fragmented across three vendor UIs and the team has to manually correlate across them.

The CTO has issued two requirements, both binding:

1. **Cut observability spend by at least 40%** within six months.
2. **Cut median time-to-root-cause by at least 30%** in the same window.

Both, simultaneously. Without losing incident-response capability or operational maturity. The team will reject any plan that pretends one of the constraints does not exist.

You produce the plan. Another engineer will execute it.

> Three things the team will reject:
> - A pure cost-cut plan that drops capabilities. "Just turn off APM" is not a plan.
> - A pure rebuild-from-scratch plan that does not show how to migrate gradually, with rollback at every step.
> - Any choice that does not justify cost, capability gap, and risk together. Cheap is easy. Cheap **and** resilient **and** faster is the design problem.

---

## 2. What you receive

| Artifact | Shape |
|---|---|
| `services.json` | 10-service application topology + 4 backing stores + 17 edges. Tier and ownership metadata per service. |
| `current-stack.md` | Inventory of every observability vendor and tool currently in use, monthly cost per line item, and what each piece is used for. |
| `incidents_history.json` | 29 historical incidents with MTTD, MTTR, root-cause class, services involved, and resolution. Use this to ground claims about what is actually breaking. |
| `pain_points.md` | A list of 10 specific operational pain points the team has surfaced. Each pain point includes context that lets you map it to one or more capabilities in the new design. |
| `current-architecture.png` | A block diagram of how observability data flows in the current stack. |

All inputs are deliberately *narrative* rather than measurement-rich. This is a design exercise. You will need to make scaling assumptions explicit and defend them, not extract them from data.

---

## 3. What you must produce (REQUIRED)

### A1. Target-state architecture diagram

One diagram. Components, data flow, ownership boundaries. Specific enough to build from. Hand-drawn + photographed is fine; draw.io or mermaid is fine.

**Must show:**
- The ingestion path for metrics, logs, traces.
- Storage and retention tier per signal type (hot vs cold).
- The alerting + correlation surface (where alerts fire, how they group).
- The human-facing query surface(s). If on-call must switch tools mid-incident, the diagram should not hide it.
- What is SaaS, what is OSS, what is built in-house. Use colour or shading.

### A2. Component-decision table

For each capability listed below, name the chosen component or vendor. For each row, two short fields: **why this one** (one sentence), and **what gets worse if we change our mind in six months** (one sentence).

Capabilities to cover (you may collapse two into one row if you defend it):
- Metrics ingestion + storage + query
- Logs ingestion + storage + search
- Distributed tracing
- Alerting (rule engine) + correlation / grouping
- Incident routing + paging
- Dashboards + SLO tracking

### A3. Cost model

A markdown table or spreadsheet — keep the math visible. One row per cost line item:

- Monthly cost today and at target state.
- The unit cost driver per row (GB ingested, host-hours, user seats).
- Assumed scale today.
- One sensitivity row: if data volume grows two times faster than projected, what breaks the budget first?

Target a credible reduction of at least 40%. "Credible" means defensible against vendor public pricing or cloud calculators — not round-number guesses. Reviewers will check two rows at random against public list price.

### A4. Two Architecture Decision Records

Pick the **two hardest** decisions in your design. For each, write one ADR (~1 page) with the standard structure:

- **Context** — what made this decision necessary.
- **Decision** — what you chose.
- **Alternatives considered + rejected** — at least two, with the reason each was rejected.
- **Consequences** — both positive and negative; honest. An ADR with no negative consequences is not an ADR; it is marketing.

Pick *hard* decisions. Moving from a SaaS log vendor to OSS is interesting. Picking Grafana over Kibana is not.

### A5. Eight-week migration plan

A week-by-week plan, eight weeks, current state → target state. Must include:

- **A rollback path on every cut-over.** If week 5 is "cut over log ingest" then week 5 also describes how the team fails back inside 30 minutes.
- **A no-observability-blackout guarantee.** At no point during business hours is the system unobservable.
- **Explicit go/no-go gates between phases.** Examples: 95% of historical alert rules reproduced; on-call independently triaged one synthetic incident; query latency under N seconds at p99.

### A6. Risk register

A table of the **six** highest risks. Per row: risk description, likelihood (low / med / high), impact (low / med / high), mitigation, owner.

The mitigation must be specific. "Buy more capacity" does not count. "Negotiate a 30-day burstable contract with a 30-day exit clause" does.

### A7. POC plan (writing, no code required)

In your `FINDINGS.md` (or a short separate doc): name the **single most uncertain** component in your target design. State the one assumption you would validate first if you had three days of engineering time after this lab, and the measurement that would confirm or deny it. One paragraph is enough.

(No Docker Compose required. You may include one if you find it clarifies the design, but the deliverable is the written validation plan.)

---

## 4. What you may produce (OPTIONAL)

Each is a deep dive on its own. Pick one if you have time.

### B. Capacity model

Forecast 12-month ingest growth using `incidents_history.json` activity patterns and assumed service-count growth. Compute the target-state cost under three growth scenarios (slow, expected, fast). Defend the scenario assumptions, not just the math.

### C. Vendor exit-clause analysis

For each current SaaS vendor, write the literal contract terms you would negotiate to enable a controlled migration: minimum exit notice, data-export format and frequency, contract length, escape clauses, source-data rights at termination. This is a real consulting skill and few engineers practise it.

### D. Skills-gap + transition plan

Today the team operates SaaS UIs. The target stack requires operational expertise in different components. Identify per role what needs to change: hiring vs. upskilling, what training materials, expected timeline, cost.

### E. Multi-region or disaster-recovery posture

Today the stack is single-region. Specify how each component in the target state replicates or fails over. Define a per-component RTO and RPO target and justify each.

---

## 5. `FINDINGS.md` — required reflection

Five questions. Answer all. Each answer must reference one or more of your own design choices with concrete numbers or component names from your own artifacts.

1. Which capability turned out hardest to replace, and why? What did you compromise on?
2. Where did your design trade resilience for cost? Quantify the trade-off — "$X saved per month at the cost of Y extra minutes MTTR in scenario Z".
3. If the budget cut requirement were 60% instead of 40%, which decisions would change and which would not? What does that tell you about the structure of cost in this stack?
4. Identify one pattern in your design that you copied from a real-world system you know. Name the system, the pattern, and what you changed.
5. What is the biggest unknown in your plan — something that could derail the migration at week N? What would you spike in the first week to de-risk it?

---

## 6. Grading rubric (100 points)

| Item | Weight |
|---|---|
| A1 architecture diagram — readable, specific, covers all signal paths and human surfaces | 10 |
| A2 component-decision table — every capability addressed, defended in one sentence each | 15 |
| A3 cost model — credible numbers, ≥40% reduction, with sensitivity | 20 |
| A4 two ADRs — non-trivial decisions, ≥2 alternatives each, consequences honest | 20 |
| A5 migration plan — every cut-over has a rollback, no observability blackout, go/no-go gates | 15 |
| A6 risk register — six rows, specific mitigations, ownership assigned | 5 |
| A7 POC plan — one component named, one assumption stated, one measurement defined | 5 |
| FINDINGS — concrete, references your own artifacts | 10 |
| Optional sections (B / C / D / E) | up to 15 bonus |

Tier thresholds: ≥85 excellent, ≥70 pass, ≥55 needs revision.

Reviewers will pull two cost-model rows at random and check against the relevant public list price. A row that misses by more than 50% costs you the row.

---

## 7. Submission

A directory containing:

```
your-name/
├── architecture-target.png         (or .mmd or .drawio — A1)
├── components.md                   (A2 — the decision table)
├── cost-model.{xlsx,csv,md}        (A3)
├── adr/
│   ├── adr-001-*.md                (A4 — two files)
│   └── adr-002-*.md
├── migration-plan.md               (A5)
├── risks.md                        (A6 — the register, six rows)
├── FINDINGS.md                     (includes A7 POC plan paragraph)
└── README.md                       (one paragraph: how to read the submission, what to look at first)
```

How you split markdown across files is your call as long as the directory above is recognisable.

---

## 8. Out of scope

- You do **not** need to actually procure anything. The submission is a plan, not a transaction.
- You do **not** need to write production code in any component you propose. The single POC under A7 is the only code expected.
- You do **not** need to negotiate real vendor contracts. Optional section C is a writing exercise, not a legal one.
- You do **not** need to recommend any specific component, vendor, or topology. You need to defend the one you pick.

---

## 9. Reference reading (selected)

Not a syllabus — a menu. The point is to see the design space so you can defend your own choice in FINDINGS.

### Books

- *Designing Data-Intensive Applications* (Kleppmann) — chapters 11 and 12 for stream processing, retention design, log economics.
- *Google SRE Book* — chapter 4 (SLOs), chapter 6 (monitoring distributed systems), chapter 17 (testing for reliability). Free online.
- *Site Reliability Workbook* (Beyer et al.) — chapter 2 for SLO implementation, chapter 5 for alerting practice.

### Blogs and writeups

- Charity Majors / Honeycomb blog — the canonical voice on observability philosophy. *"Observability is not three pillars"* is required.
- Liz Fong-Jones — on tracing economics and on-call sustainability.
- Cindy Sridharan — *Distributed Systems Observability*.
- Datadog, Dynatrace, New Relic public engineering blogs — for the SaaS perspective on what they sell. Read critically.
- Recent public pricing pages for Datadog, Splunk, New Relic, Sumo Logic, PagerDuty — for cost realism in A3.

### Open-source projects worth surveying

- Prometheus / VictoriaMetrics / Mimir — metrics ingest and storage at different scales.
- Loki / OpenSearch / ClickHouse — log storage at different cost / search trade-offs.
- Tempo / Jaeger / SkyWalking — distributed tracing with different operational shapes.
- Grafana / Perses — query and visualisation layers.
- Alertmanager / Robusta — alert routing and grouping engines.
- OpenTelemetry Collector — the universal ingestion / processor / exporter pipeline. Worth understanding even if your design routes around it.
- Coroot — OSS observability stack with built-in RCA. Look at its architecture as a reference for cohesive OSS design.

### Design-thinking hints

These are angles to consider — they will not tell you what to do.

- **Storage cost is dominated by ingest volume, not query volume.** This shapes the entire cost game. Most observability cost-reduction is sampling, summarisation, and tiered retention — not picking a cheaper vendor for the same volume.
- **Logs are the worst signal-to-cost ratio in observability.** Many teams spend 60% of their bill on logs and use 5% of them. Aggressive log handling — sampling, structured-only retention, ephemeral hot tier — usually drives the largest cuts.
- **APM and tracing buys you the most diagnostic speed per dollar.** If your goal is MTTR reduction, do not gut tracing. If your goal is cost reduction, look at logs first.
- **Alerting is where SaaS lock-in actually hurts.** Migrating alert rules is more painful than migrating dashboards because the rules encode tribal knowledge. Migration planning should respect that.
- **You will overestimate how cheap OSS is.** Self-hosted OSS has operational cost — people-hours, infrastructure, on-call burden for the observability stack itself. Senior architects always include this; juniors often forget.
- **The hardest constraint is usually not the loudest one.** "Cut 40% spend" is loud. "Do not regress on-call quality" is quiet but ultimately what gets the design rejected.

---
