# Lab — Observability + AIOps Stack Redesign — Data Pack

This pack contains the inputs you need to do the architecture lab.

## Contents

```
data-pack/
├── services.json              The 10-service topology + 4 stores + 17 edges
├── current-stack.md           Vendor inventory + monthly cost breakdown
├── incidents_history.json     29 historical incidents (MTTD / MTTR / class / actions)
├── pain_points.md             10 operational pain points to address in your design
├── current-architecture.png   Block diagram of how data flows today
└── README.md                  This file
```

## How to read these inputs

Start with `current-architecture.png` to see the data flow today. Then read `current-stack.md` to understand what each piece does and how much it costs. Then `pain_points.md` to understand what is actually broken. Finally browse `incidents_history.json` to ground your assumptions about what kind of incidents the system actually faces.

You are **not** expected to inspect `incidents_history.json` programmatically. Reading the file as JSON in your editor and skimming is sufficient.

## Inputs you are explicitly NOT given

This is design work, not measurement work. You will need to make scaling assumptions explicit and defend them. You will not find tables of latency percentiles or ingest rate timeseries here — make the assumption, write it down, defend it.

## What you produce

See the handout for the full deliverable list. In short: one target-state architecture diagram, one component-decision table, one cost model, three ADRs, one twelve-week migration plan, one risk register, one local POC, and `FINDINGS.md`.
