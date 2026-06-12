# Lab — Evidence-Driven Remediation Engine — Data Pack

## Quick Start

```bash
# Run engine on all 8 test cases
python engine.py decide --incident eval/E01.json --history incidents_history.json --actions actions.yaml --audit audit.jsonl
python engine.py decide --incident eval/E02.json --history incidents_history.json --actions actions.yaml --audit audit.jsonl
# ... repeat for E03-E08

# Grade results
python grade.py --audit audit.jsonl --expected eval/expected.json
```

## Files

**Required:**
- `engine.py` - Main entry point (3-layer pipeline)
- `features.py` - Layer 1: incident feature extraction
- `retrieval.py` - Layer 2: historical matching & voting
- `decision.py` - Layer 3: EV-based action selection
- `optional_helpers.py` - Schema parsing utilities

**Data:**
- `eval/E01.json - E08.json` - Test incidents
- `incidents_history.json` - Historical incident corpus
- `actions.yaml` - Remediation action catalog
- `topology.json` - Service topology

**References:**
- `HANDOUT.md` - Lab requirements & algorithms
- `FINDINGS.md` - Design decisions & empirical results
- `OPTIMIZATION_REPORT.md` - Performance analysis
