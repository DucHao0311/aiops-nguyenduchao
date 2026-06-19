# Lab — Incident Forensics: 30 Days, 5 Incidents

## Overview

You have been handed a frozen 30-day snapshot of telemetry from a
production e-commerce platform. The window contains **five incidents**.
Your job has two parts:

1. **Discover** the five incident windows from the data.
2. **Analyze** each one: identify the root cause and document the
   alternative hypotheses you considered and dismissed.

A complete RCA includes both the chosen root cause and a defended set
of alternatives. Naming a root cause without showing the reasoning
that ruled out other candidates is not a complete RCA.

## Scenario

**Platform**: `ronki-shop` — a 5-service e-commerce backend with two
third-party dependencies, deployed across three availability zones.

**Topology** (full graph in `data-pack/topology.json`):

```
frontend → api-gateway → checkout-svc ┬→ payment-svc → rds-orders
                                      │              → fx-api    (3rd-party)
                                      │              → pp-api    (3rd-party)
                                      ├→ inventory-svc → rds-inventory
                                      │                → warehouse-api (3rd-party)
                                      └→ redis-cache

Service mesh (mTLS, 24h cert rotation).
AZs: a, b, c.
```

**Window**: 2026-03-01 00:00 UTC → 2026-03-30 23:59 UTC. All timestamps
are UTC.

**What you know**: the 30-day window contains exactly **five
incidents**. Each lasts between 15 minutes and a few hours. Each is
self-resolved or auto-mitigated; no operator notes are in the data
pack.

The window also contains **routine ops events** — scheduled
maintenance, batch jobs, periodic syncs — that can produce metric
anomalies and may even fire an alert. These are **not incidents** and
must not be reported as such. Distinguishing real incidents from
routine ops noise is part of the lab.

## Data Pack

All evidence lives under `data-pack/`. You may read every file. No
external lookups are required or permitted; the pack is self-contained.

### Inventory

| Path | Format | Contents |
|---|---|---|
| `data-pack/topology.json` | JSON | Service graph, third-party endpoints, AZ list, mesh metadata |
| `data-pack/alerts.json` | JSON array | All alerts that fired during the window |
| `data-pack/deploy_log.json` | JSON array | Deploys, config changes, infrastructure events, vendor announcements |
| `data-pack/traces.json` | JSON array | Sampled distributed traces |
| `data-pack/logs/{service}.jsonl` | JSONL | Structured logs per service, one event per line |
| `data-pack/metrics/{name}.csv` | CSV (`ts,value`) | Time-series metrics at 1-minute resolution |

### Log fields

Each line in `data-pack/logs/*.jsonl` is a JSON object:

```json
{"ts": "2026-03-22T09:00:00.779Z", "level": "ERROR",
 "service": "payment-svc", "component": "payment_processor_client",
 "msg": "...", "trace_id": "...", "extra": {...}}
```

Fields:

- `ts` — UTC ISO-8601 with millisecond precision
- `level` — `DEBUG`, `INFO`, `WARN`, `ERROR`
- `service` — emitting service (matches a node in `topology.json`)
- `component` — sub-component within the service
- `msg` — free-form message slug
- `trace_id` — optional, present on request-path logs
- `extra` — optional structured fields; key set varies by event type

### Metric files

All metrics are sampled at 1-minute resolution.

| File | Meaning | Unit |
|---|---|---|
| `checkout_p99_ms.csv` | checkout-svc p99 request latency | ms |
| `checkout_error_rate.csv` | checkout-svc error fraction | 0.0–1.0 |
| `payment_memory_pct.csv` | payment-svc heap usage | percent |
| `payment_conn_pool_active.csv` | payment-svc active outbound HTTP connections | count |
| `payment_retries_per_min.csv` | payment-svc outbound retries | per minute |
| `payment_az_a_error_rate.csv` | payment-svc error rate, AZ-a traffic | 0.0–1.0 |
| `payment_az_b_error_rate.csv` | payment-svc error rate, AZ-b traffic | 0.0–1.0 |
| `payment_az_c_error_rate.csv` | payment-svc error rate, AZ-c traffic | 0.0–1.0 |
| `rds_cpu_pct.csv` | rds-orders CPU | percent |
| `rds_audit_writes_per_min.csv` | writes to `audit_log` table | per minute |
| `rds_query_p99_ms.csv` | rds-orders query p99 | ms |
| `redis_hit_rate.csv` | Redis cache hit rate | 0.0–1.0 |
| `redis_evictions_per_min.csv` | Redis eviction count | per minute |
| `inventory_p99_ms.csv` | inventory-svc p99 request latency | ms |
| `inventory_memory_mb.csv` | inventory-svc RSS | MB |
| `inventory_oom_kills_per_min.csv` | container OOM-kill events | per minute |
| `inventory_warehouse_health.csv` | warehouse-api health probe | 0=down, 1=healthy |
| `fx_api_5xx_per_min.csv` | 5xx responses from fx-api | per minute |
| `mtls_handshake_failures_per_min.csv` | mTLS handshake errors | per minute |
| `frontend_req_rate.csv` | frontend incoming request rate | req/sec |

## Required Deliverables

Submit the following files in your repository under `lab-rca-forensics/`:

### 1. `RCA_REPORT.md`

A single report covering all five incidents. Use this structure:

#### Header

A short table listing your five identified incidents, sorted by start
time:

| Incident ID | Window (UTC) | One-line summary |
|---|---|---|
| I-? | 2026-03-?? HH:MM → HH:MM | ... |

You assign your own incident IDs. Naming convention: `I-1`, `I-2`, etc.

#### Per-incident section (repeat for each of the five)

For each incident, include the following six subsections:

1. **Timeline** — Ordered list of significant events with UTC
   timestamps. Each entry must cite its data source (log line, metric
   file + approximate timestamp, or alert ID).
2. **Candidate Hypotheses** — At least three plausible root cause
   hypotheses, each with a 1–5 confidence rating and one sentence of
   initial reasoning.
3. **Evidence Review** — For each candidate, accept or dismiss it.
   Dismissals must cite at least one piece of evidence from the data
   pack. Use this structure:
   > **Hypothesis**: ...
   > **Verdict**: Accepted / Dismissed
   > **Evidence**: ...
4. **Root Cause** — A paragraph stating the root cause, followed by a
   sequence diagram or ordered prose showing the causal chain.
5. **Counterfactual** — For each dismissed hypothesis, one sentence
   explaining why "fixing" that thing would not have resolved this
   incident.
6. **Prevention** — Two concrete, testable recommendations to prevent
   recurrence of this incident. Each must name a specific service or
   component and state a measurable change.

#### Non-incident events ruled out

After the five incident sections, add a section titled **Non-incident
events ruled out**. List at least two candidate anomalies you
examined and determined were *not* incidents. For each, give:

- The time window
- The signal that drew your attention (metric divergence, alert,
  or log)
- The evidence that ruled it out as a routine ops event (deploy_log
  entry, recurring pattern, completion log line, or other)

Empty section, or fewer than two ruled-out events, costs credit on
the non-incident discrimination dimension.

### 2. `correlator.py`

A Python script invoked as `python correlator.py ../data-pack/` that
reads the data pack and prints grouped alerts to stdout in this format:

```
CHAIN 1: <root_alert_name> (<service>) @ <timestamp>
  originator: <service>.<component> @ <timestamp>
    first trouble: <level> <msg> (upstream: <endpoint>)
  child alerts:
    ├─ <child_alert_name> (<service>) (+<delta>s)
    ...

CHAIN 2: ...
```

A "chain" groups alerts that share a causal relationship. Temporal
proximity alone is **not** sufficient. At least one explicit signal
(shared `trace_id`, shared `component`, dependency edge from
`topology.json`) must justify each grouping. Include a docstring
explaining your grouping heuristic.

The script must derive groupings programmatically; do not hardcode
alert names or incident windows.

### 3. `evidence_graph.png` and `evidence_graph.py`

A directed graph image showing the causal chain for each incident.
Nodes are services or components; edges are labeled with the
propagation mechanism. Generate it programmatically with `matplotlib`
+ `networkx`. Include both the generation script and the resulting
PNG (a single PNG with five subplots, or five separate PNGs named
`evidence_graph_I-N.png` — either is acceptable).

### 4. `SUBMIT.md`

A reflection under 500 words covering:

- Which incident was hardest to diagnose, and why
- One hypothesis you initially considered and what changed your mind
- The single most useful file in the data pack and why
- One blind spot in the data pack that, if added, would have shortened
  your analysis

## Evaluation Criteria

Each incident is scored independently on five dimensions, each
weighted equally. Final lab score is the average across all five
incidents.

| # | Dimension | What earns credit |
|---|---|---|
| 1 | Timeline reconstruction | Every entry has a timestamp and a citation; ordering is correct |
| 2 | Hypothesis enumeration | At least three distinct candidates with non-trivial reasoning |
| 3 | Alternative-hypothesis dismissal | Each dismissed hypothesis is refuted with a specific piece of evidence, not handwaved |
| 4 | Root cause identification | Correct root, with a causal chain that follows from the data pack |
| 5 | Counterfactual reasoning | For each dismissed candidate, a concrete reason "fixing" it would not have resolved the user-visible symptom |

A separate **non-incident discrimination** score (also 1–5) is
computed once across the whole report based on the "Non-incident
events ruled out" section: completeness of candidates examined,
quality of evidence cited, and absence of incidents incorrectly
classified as non-incidents.

**Missing an incident** counts as a full zero on all five dimensions
for that incident. **Misidentifying an incident** (writing a full
per-incident section for a non-incident time window) deducts from
your final score and also reduces the non-incident discrimination
score.

## Suggested Working Method

You are not required to follow these steps, but they have proven
effective in incident review:

1. Start with `alerts.json` — alerts cluster around incidents.
2. Sweep `metrics/*.csv` looking for any value that diverges from its
   baseline; for each anomaly, ask whether it correlates with an
   alert cluster.
3. For each candidate incident, define a tight window (typically 30
   min before the first alert through 30 min after the last) and
   load logs filtered to that window.
4. Find the earliest non-INFO log line in the relevant service's
   window. The structured fields on that line often carry the most
   information.
5. When two metrics move together, ask which one is upstream of the
   other in the call graph — that ordering determines cause vs. effect.
6. Resist anchoring on the first or loudest signal; alert volume is
   not proportional to causal importance.
7. Read `deploy_log.json` at the start of the lab and revisit it when
   analyzing each incident. Most entries are routine; correlate them
   against your candidate windows.

## Environment

Recommended Python packages: `pandas`, `numpy`, `matplotlib`,
`networkx`. Install with:

```
pip install pandas numpy matplotlib networkx
```

The data pack is static. No services need to be started. All analysis
happens offline.

## Constraints

- All timestamps in the data pack are UTC
- The data pack is deterministic; re-reading the same file gives
  identical content
- Network access is neither required nor expected
- Do not hardcode the answer in `correlator.py`; the script must
  derive groupings from the data
- You may use any analysis tool (pandas, jq, awk, grep); clarity of
  citation matters more than tool choice

## File Tree

```
lab-rca-forensics/
├── RCA_REPORT.md
├── correlator.py
├── evidence_graph.py
├── evidence_graph.png        (or evidence_graph_I-1.png … I-5.png)
└── SUBMIT.md
```

Good luck.
