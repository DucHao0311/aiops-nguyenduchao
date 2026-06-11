# AIOps Workshop

## Setup

```bash
unzip workshop-student-*.zip
cd workshop-student
uv venv --python 3.12
uv pip install -r requirements.txt
```

## Run

Open notebooks in order:

```bash
.venv/bin/jupyter notebook exercises/
```

### `ex01-detect-precursor.ipynb`
Anomaly detection on a slow-burn ESB connection-pool exhaustion (scenario S01). 3σ vs IsolationForest, plus precursor metric (`conn_pool_used`).

### `ex02-correlate-rca.ipynb`
Multi-ranker root cause analysis on a 4-hop cascade (scenario S06). PageRank, earliest-drift, drift-count, Granger causality, weighted RRF fusion.

### `ex03-closed-loop.ipynb`
Live event stream + decision logic + pre-incident forecast (S08, S10). **Needs the API server running** in another terminal:

```bash
.venv/bin/python stack/api.py
```

Then open `http://localhost:8000/` in your browser to see the live dashboard while the notebook runs.
