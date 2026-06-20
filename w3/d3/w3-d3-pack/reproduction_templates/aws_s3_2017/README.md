# AWS S3 us-east-1 (2017-02-28) — Operator Typo Reproduction

**Original:** https://aws.amazon.com/message/41926/

A maintenance command intended for the billing subsystem was mistyped; the
input did not narrowly target billing, so the command removed servers from
the S3 index and placement subsystems as well. Both subsystems are required
for any S3 operation → 4h outage.

## Failure mode
- **Class:** operator typo + over-broad command scope
- **Pattern:** insufficient blast radius on destructive ops

## Setup
```bash
docker compose up -d        # billing + index + placement (3 alpine containers)
docker compose ps           # all 3 up
```

## Inject
```bash
bash inject.sh              # simulates "stop --remove-orphans" without target filter
docker compose ps           # all 3 gone
```

## What to observe in your AIOps pipeline
- Does it detect the simultaneous disappearance of 3 services?
- Can RCA tell the operator command apart from infrastructure failure?
- What signal would distinguish a real failure from an intentional ops action?
