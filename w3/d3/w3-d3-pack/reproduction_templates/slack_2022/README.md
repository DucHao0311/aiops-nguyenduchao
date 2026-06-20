# Slack Post-Holiday Provisioning Overload (2022-01-04) — Design-Only

**Original:** https://slack.engineering/slacks-incident-on-2-22-22/

Post-holiday traffic recovery caused all clients to reconnect simultaneously.
The provisioning subsystem (responsible for routing clients to backend pools)
became the bottleneck; cascading retries amplified load.

This reproduction is **design-only** — a faithful repro needs a load
generator capable of millions of concurrent connection attempts, which is
out of scope for a starter pack.

## Failure mode
- **Class:** capacity provisioning overload + retry amplification
- **Pattern:** thundering herd → retries → exponential load growth

## Design
1. Build a simple "router" service that maps client_id → backend pool
2. Build a client pool of 1000 simulated clients (long-poll connections)
3. Inject: drop all router connections at once → 1000 clients reconnect at once
4. With retry-on-failure logic: failure during reconnect → exponential growth in pending requests

## What to observe (theoretically)
- Does the pipeline detect the request-volume spike before queue depth saturates?
- Distinguishing thundering herd from real traffic spike requires history (W2 Lab C)

## Deliverable for this outage
Write a 2-page SPEC describing reproduction + AIOps signal design. Postmortem still required.
