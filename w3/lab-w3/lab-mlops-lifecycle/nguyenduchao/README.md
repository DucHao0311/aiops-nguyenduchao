# README — MLOps Lifecycle: Anomaly Detection Pipeline

**Tác giả:** nguyenduchao

Pipeline phát hiện model decay trong payment gateway metrics, tự động trigger retrain, và swap model mới vào production via MLflow Registry alias — không downtime, không mất observability.

---

## Cách chạy từ đầu đến cuối

### 1. Khởi động stack (MLflow + PostgreSQL + Prometheus + Grafana)

```bash
cd data-pack
bash scripts/start_stack.sh
# Chờ ~30s lần đầu (Postgres init + MLflow install psycopg2-binary)
```

Verify:
```bash
curl -s http://localhost:5000/health   # MLflow
curl -s http://localhost:9090/-/healthy # Prometheus
curl -s http://localhost:3000/api/health # Grafana
```

### 2. Cài dependencies Python

```bash
uv pip install "mlflow==2.13.2" "evidently==0.4.40" scikit-learn pandas numpy fastapi uvicorn prometheus_client requests
```

### 3. Sinh data (deterministic, seed=42)

```bash
uv run python data-pack/data/generate_data.py
```

### 4. Train model v1 + register vào MLflow

```bash
export MLFLOW_TRACKING_URI=http://localhost:5000
uv run python nguyenduchao/pipeline.py --data data-pack/data/baseline.csv
# Output: [pipeline] Registered  : anomaly-detector v1 → alias 'production'
```

### 5. Khởi động serve.py (terminal riêng)

```bash
export MLFLOW_TRACKING_URI=http://localhost:5000
uv run python nguyenduchao/serve.py
```

Kiểm tra:
```bash
curl -s http://localhost:8000/health/active-version
# {"model_name":"anomaly-detector","version":"1","alias":"production","model_uri":"models:/anomaly-detector@production"}

curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"features": [[120.5, 0.8, 450.0], [350.0, 5.2, 900.0]]}'
```

### 6. Chạy drift detection

```bash
# Data drift only (Acceptance Criterion 1-3):
uv run python nguyenduchao/drift_detector.py \
    --reference data-pack/data/baseline.csv \
    --current data-pack/data/drifted.csv \
    --threshold 0.15

# Combined mode (Acceptance Criterion 4 — bắt buộc):
uv run python nguyenduchao/drift_detector.py \
    --reference data-pack/data/baseline.csv \
    --current data-pack/data/drifted.csv \
    --check-mode combined \
    --labeled-current data-pack/data/drifted.csv \
    --model-uri models:/anomaly-detector@production
# Output phải có cả "Drift score" VÀ "Perf precision"
```

### 7. Chạy retrain pipeline đầy đủ (tất cả Acceptance Criteria)

```bash
# Đầy đủ: holdout validation + approval gate + post-deploy monitor + auto-rollback
uv run python nguyenduchao/retrain.py \
    --reference data-pack/data/baseline.csv \
    --current data-pack/data/drifted.csv \
    --holdout data-pack/data/holdout.csv \
    --post-deploy-eval data-pack/data/post_deploy_eval.csv \
    --serve-url http://localhost:8000

# Khi prompt hiện ra, gõ 'y' để approve promote staging → production
```

Expected output chính:
```
[retrain] Sliding window rows : 5328 (baseline 4320 + drift 1008)
[retrain] Holdout validation — v2 precision: X.XXXX  recall: X.XXXX   ← Acceptance Criterion 5
[post_deploy_monitor] Cycle 01/24 — precision: X.XXXX  recall: X.XXXX ← Acceptance Criterion 6
...
```

### 8. Xem observability dashboard

Grafana: http://localhost:3000 → dashboard "AIOps MLOps Lifecycle"

### 9. Dừng stack

```bash
bash data-pack/scripts/stop_stack.sh
```

---

## Cấu trúc files

```
nguyenduchao/
├── pipeline.py        Train IsolationForest + MLflow register @production
├── serve.py           FastAPI /predict + /health/active-version + /reload + /metrics
├── drift_detector.py  Evidently DataDriftPreset + performance drift (--check-mode combined)
├── retrain.py         Orchestrator: drift→sliding window train→holdout→staging→approval→promote→post-deploy
├── metrics_util.py    Prometheus Pushgateway helpers
├── DESIGN.md          Design defense (7 sub-checkpoints)
├── SUBMIT.md          Reflection (5 câu)
└── README.md          (file này)
```

## Acceptance Criteria checklist

| Criterion | Command để verify |
|---|---|
| 1-3: Train/Serve/Drift | Steps 4-6 ở trên |
| 4: Combined drift mode | `drift_detector.py --check-mode combined` — output có cả "Drift score" VÀ "Perf precision" |
| 5: Holdout validation | `retrain.py --holdout holdout.csv` — output có "Holdout validation — v2 precision: X.XXXX" |
| 6: Auto-rollback | `retrain.py --post-deploy-eval post_deploy_eval.csv` — output có "Cycle XX/24" và có thể "Rollback complete" |
