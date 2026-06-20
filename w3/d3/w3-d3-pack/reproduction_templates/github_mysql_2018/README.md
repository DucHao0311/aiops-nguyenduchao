# GitHub MySQL Split-Brain (2018-10-21) — Network Partition Reproduction

**Original:** https://github.blog/2018-10-30-oct21-post-incident-analysis

A 43-second network partition caused MySQL Orchestrator (failover tool) to
promote a replica in another region. When connectivity returned, both sides
believed they were primary; reconciling 24 hours of writes took 24h11m.

## Failure mode
- **Class:** split-brain via orchestration tool reacting too fast
- **Pattern:** failover threshold shorter than mean partition duration

## Setup
```bash
docker compose up -d        # mysql-primary + mysql-replica + simulated orchestrator
docker compose ps           # all 3 up, replication is running
```

## Inject (simplified — only network partition, no actual MySQL promotion)
```bash
bash inject.sh              # disconnects orchestrator from primary's network for 43s
```

The simulated orchestrator (a shell loop) will log "primary unreachable →
promoting replica". Real MySQL promotion is omitted to keep the demo simple;
the postmortem-relevant signal is the orchestrator's decision logic.

## What to observe in your AIOps pipeline
- Does the detector pick up the partition (network metric vs application metric)?
- Can RCA distinguish a partition from a primary crash?
- What guardrail would have prevented promotion in this case?
