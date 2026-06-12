# Lab — Evidence-Driven Remediation Engine — Data Pack

This pack contains everything you need to run the lab described in the handout.

## Contents

```
data-pack/
├── eval/
│   ├── E01.json ... E08.json          (8 evaluation incidents)
│   └── expected.json                  (ground-truth accepted actions)
├── incidents_history.json             (~29 past incidents)
├── topology.json                      (canonical service topology)
├── actions.yaml                       (remediation action catalog)
├── grade.py                           (auto-grader — run after you produce audit.jsonl)
├── engine_skeleton.py                 (optional starting skeleton — feel free to ignore)
├── optional-helpers.py                (two pure-mechanical schema parsers — see HANDOUT §2.6)
└── README.md                          (this file)
```

## Quick start

```bash
unzip lab-w2-evidence-driven-remediation-*.zip
cd data-pack
uv venv --python 3.12 && uv pip install pandas numpy scikit-learn pyyaml
# Write your engine.py, features.py, retrieval.py, decision.py.
# Run on each eval incident:
for i in 01 02 03 04 05 06 07 08; do
  .venv/bin/python engine.py decide --incident eval/E$i.json \
                              --history incidents_history.json \
                              --actions actions.yaml
done
# Auto-grade your audit.jsonl:
.venv/bin/python grade.py --audit audit.jsonl --expected eval/expected.json
```

## Reading the schemas

- `eval/E*.json` — see handout §2.1.
- `incidents_history.json` — see handout §2.2.
- `actions.yaml` — see handout §2.3.
- `eval/expected.json` — `accepted_actions` is a list; engine recommending any one of them gets credit. `must_not_action` is a hard veto.
- `topology.json` — same structure as `eval/E*.json.topology` (nodes + edges).

## Submission

See handout §7.
