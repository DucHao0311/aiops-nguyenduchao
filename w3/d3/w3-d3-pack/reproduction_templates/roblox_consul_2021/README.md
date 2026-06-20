# Roblox Consul + BoltDB (2021-10-28) — Design-Only Reproduction

**Original:** https://about.roblox.com/newsroom/2022/01/roblox-return-to-service-10-28-10-31-2021

A 73-hour outage caused by interaction of three things:
1. Consul streaming write contention under heavy load
2. BoltDB freelist algorithm degrading non-linearly as DB grew
3. Monitoring itself depending on the failing Consul cluster, hiding the signal

This reproduction is **design-only** — a faithful reproduction needs a real
Consul cluster + multi-day load, which is out of scope for a starter pack.
Below is the experiment design instead.

## Failure mode
- **Class:** monitoring loop (Pattern §4.4 — observer depends on observed system)
- **Pattern:** circular dependency between control plane and its monitoring

## Design (write SPEC.md instead of running)
1. Start a 3-node Consul cluster + a service that writes 1000 KV/s
2. Slowly grow KV store to 4 GB (simulate days of writes)
3. Observe streaming endpoint latency: should degrade O(n²) past 2 GB
4. Critically: route Prometheus scrape config through Consul service discovery
5. When Consul slows → Prometheus loses targets → alert pipeline goes blind

## What to observe (theoretically)
- Pipeline blind spot when monitoring depends on the failing system
- Detection signal must be **out-of-band** of the system being monitored

## Deliverable for this outage
Write a **2-page SPEC** describing how you would reproduce + what AIOps signal
would catch the monitoring loop. Postmortem still required.
