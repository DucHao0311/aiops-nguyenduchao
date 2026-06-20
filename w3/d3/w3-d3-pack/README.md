# W3-D3 Postmortem + Outage Reproduction — Starter Pack

This is a **starter pack**. It ships:

- 5 outage reproduction skeletons (3 with runnable docker-compose, 2 design-only)
- Templates for postmortem, ADR, SPEC, cost model
- Timeline capture script
- Outage catalog metadata

It does NOT ship:

- A working AIOps pipeline runtime (FastAPI service on port 8000). The exercise
  assumes you wire your pipeline from your own W1+W2 notebook code or ask
  the trainer for a sample container.

## Layout

```
README.md
outage_catalog.yaml                  # metadata for §5 outages
templates/
├── postmortem_template.md           # Google SRE format (§2)
├── adr_template.md                  # Nygard format (§7.1)
├── spec_template.md                 # SPEC.md outline (§9.8)
└── cost_model_template.py           # break-even calculator (§8)
reproduction_templates/
├── aws_s3_2017/                     # runnable: 3-service typo demo
├── cloudflare_regex_2019/           # runnable: regex CPU pin
├── github_mysql_2018/               # runnable: MySQL split-brain via network
├── roblox_consul_2021/              # design-only README (HARD reproduction)
└── slack_2022/                      # design-only README (provisioning overload)
scripts/
├── start_reproduction.sh            # stub — calls reproduction_templates/<X>/up.sh
├── inject.sh                        # stub — calls reproduction_templates/<X>/inject.sh
└── capture_timeline.py              # event timeline → timeline.json
```

## Quick test (templates only — no pipeline)

```bash
cd reproduction_templates/aws_s3_2017
docker compose up -d
bash inject.sh           # 3 containers go down
docker compose ps
docker compose down
```
