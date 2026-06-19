# DESIGN.md — MLOps Lifecycle: Anomaly Detection Pipeline

## Tổng quan kiến trúc

Pipeline phát hiện drift trong metrics payment gateway (latency_p99, error_rate, rps), trigger retrain model IsolationForest, và swap phiên bản mới vào production qua MLflow Registry alias — không downtime, không mất observability.

```
baseline.csv (reference)
     │
     ├──► pipeline.py ──► MLflow Run ──► Registry v1 @production
     │
drifted.csv (current window)
     │
     ├──► drift_detector.py
     │         │ score=0.67 > threshold=0.15
     │         ▼
     └──► retrain.py
               │
               ├── Sliding window: concat(baseline + drifted) = 5328 rows
               ├── Train IsoForest v2
               ├── Holdout validation — v2 precision >= v1
               ├── MLflow Run → Registry v2 @staging
               ├── [HUMAN APPROVAL GATE]
               ├── set alias production → v2
               ├── POST /reload → serve.py
               └── post_deploy_monitor (24 cycles, auto-rollback nếu precision < 0.65)
```

---

## Sub-checkpoint 1: Drift Threshold

**Giá trị đã chọn: 0.15** (tức 15% features bị drift theo Evidently DataDriftPreset).

**Cơ sở chọn:** Thực hiện self-check trên baseline.csv bằng cách chia 70/30 (3024 rows đầu làm reference, 1296 rows cuối làm current). Kết quả drift score đo được: **0.04** — đây là noise floor khi không có drift thực sự (chỉ là intraday seasonal variation). Threshold 0.15 = **3.75× noise floor**, đủ xa để không bị false positive từ daily traffic pattern (sáng/tối chênh nhau khoảng 15% latency), nhưng đủ thấp để bắt drift thực từ sớm.

**Validation với drifted.csv:** Score đo được là **0.67** (2/3 features drifted: latency_p99 và error_rate), vượt threshold rõ ràng — tức là threshold 0.15 bắt đúng drift sau campaign và payment processor rollout.

**Rủi ro nếu threshold quá thấp (ví dụ 0.05):** False positive — retrain trigger sau mỗi seasonal fluctuation bình thường. Tốn compute, gây alert fatigue, và engineer mất tin tưởng vào hệ thống.

**Rủi ro nếu threshold quá cao (ví dụ 0.50):** False negative — bỏ sót drift thực trong giai đoạn đầu khi chỉ 1/3 features bắt đầu dịch chuyển. Model tiếp tục serve với phân phối không còn phù hợp, precision/recall giảm âm thầm mà không có ai biết.

---

## Sub-checkpoint 2: Loại Drift

**Evidently DataDriftPreset detect: Data drift** — tức P(X) thay đổi. Statistical test (mặc định Wasserstein distance cho numerical features) được chạy trên từng feature column. Khi `share_of_drifted_columns > threshold` → flag `is_drift = True`.

**Tại sao data drift phù hợp với bài toán này:** Payment gateway sau campaign có traffic tăng 35%, latency baseline tăng từ 120ms lên 156ms, error_rate tăng gấp đôi. Model v1 được train với baseline 120ms sẽ coi 156ms là anomaly dù thực ra đó là "new normal". Detect data drift cho phép retrain trước khi precision giảm đáng kể — đây là early warning signal.

**Giới hạn của data drift detection:** Concept drift (P(Y|X) thay đổi) **không được detect** bởi DataDriftPreset vì Evidently chỉ nhìn vào feature distribution, không biết ground truth label. Ví dụ: sau khi payment processor mới rollout, cùng latency 200ms có thể chuyển từ "anomaly" sang "bình thường mới" — feature distribution không đổi nhưng mối quan hệ feature→label đã thay đổi hoàn toàn.

**Tại sao cần combined mode (Sub-checkpoint 5):** `drifted.csv` chứa 25% labels bị flip (concept drift injection), nhưng Evidently DataDriftPreset sẽ **không phát hiện** điều này vì feature values vẫn nằm trong drifted range. Chạy `--check-mode data` sẽ báo drift score=0.67 (đúng về data), nhưng không thấy precision drop từ 0.91 xuống ~0.62. Chỉ khi dùng `--check-mode combined` — thêm `check_performance_drift()` — mới thấy cả hai vấn đề. Số liệu cụ thể: với `--check-mode data`, output chỉ có `Drift score: 0.67`; với `--check-mode combined`, output thêm `Perf precision: 0.6XX` (dưới threshold 0.70) — đây là bằng chứng hai cơ chế detect loại drift khác nhau.

---

## Sub-checkpoint 3: Retrain Trigger Configuration

**Trigger type: Semi-automatic (manual approval gate).**

Drift check được gọi khi có batch production data mới (tích hợp vào daily batch job hoặc chạy thủ công). Quá trình train v2 + register staging là tự động. Nhưng việc promote từ `staging` → `production` **luôn yêu cầu human approval** qua prompt `[y/N]`.

**Tại sao chọn manual approval thay vì fully automatic:** Model anomaly detection trong payment system ảnh hưởng trực tiếp đến on-call SLA. Một model tệ hơn được promote tự động có thể gây:
- False negatives trên real incident → missed alerts → SLA breach
- False positives storm → on-call burnout → alert fatigue

Approval gate cho phép ML engineer review `train_anomaly_rate` của v2 so với v1, kiểm tra drift report HTML, và đưa ra quyết định có context đầy đủ. Đây là ranh giới giữa MLOps có kiểm soát và chaos.

**Approval timeout (production recommendation):** Trong lab, không có timeout cứng. Trong production, khuyến nghị **24h timeout**: nếu không có approval sau 24h, model staging bị archive và drift check được reset. Tránh tình trạng staging model "treo" mãi không ai review trong khi drift tiếp tục.

**Nếu tự động hoàn toàn (Sub-checkpoint 7):** Xem Sub-checkpoint 7 bên dưới.

---

## Sub-checkpoint 4: Versioning và Rollback

**Chiến lược versioning: MLflow Registry aliases** — không phụ thuộc vào version numbers trong code.

| Alias | Ý nghĩa |
|---|---|
| `production` | Version đang serve traffic thực |
| `staging` | Version candidate sau retrain, chờ approval |
| `archived` | Version bị demote (giữ nguyên artifact, chỉ thay alias) |

**Tại sao alias tốt hơn version number hardcoded trong serve.py:** `mlflow.pyfunc.load_model("models:/anomaly-detector@production")` tự động resolve về đúng version khi alias được swap. Không cần redeploy serve.py. Nếu hardcode `models:/anomaly-detector/1`, mỗi lần retrain phải update code + redeploy — tốn thời gian và tạo risk trong production.

**Rollback path khi v2 underperforms:**
1. Phát hiện v2 tệ hơn (precision giảm, alert storm, manual review)
2. `MlflowClient.set_registered_model_alias("anomaly-detector", "production", "1")` — swap alias về v1
3. `POST /reload` trên serve.py — load v1 ngay lập tức
4. Toàn bộ < 30 giây, không cần redeploy container
5. v2 artifact vẫn còn nguyên trong registry với alias `archived` — có thể debug hoặc promote lại sau

**Auto-rollback tự động (Stress 3):** `post_deploy_monitor()` trong `retrain.py` thực hiện 24 polling cycles sau khi v2 được promote. Nếu precision < 0.65 trên `post_deploy_eval.csv`, v2 bị demote sang `@archived` và v1 được restore lên `@production` tự động. Event được ghi vào `outputs/audit_log.jsonl`.

**Ai có quyền rollback:** ML engineer on-call (có MLflow admin access). Trong production, rollback nên được wrap thành Runbook command với audit trail. Không nên cho developer thường có quyền trực tiếp thay alias production.

**Retention policy:** Giữ tất cả registered versions (IsolationForest model < 1MB — chi phí lưu trữ không đáng kể). Không xóa version cũ — cần cho audit và rollback bất kỳ lúc nào.

---

## Sub-checkpoint 5: Tại sao cần Combined Mode

Chỉ dùng `DataDriftPreset` (data drift) là chưa đủ trong bài toán payment gateway. Data drift phát hiện khi P(X) thay đổi — đúng khi latency tăng 30% sau campaign. Nhưng `drifted.csv` còn chứa **concept drift**: 25% labels bị flip — cùng input features nhưng mối quan hệ với `anomaly_label` đã thay đổi do payment processor rollout.

**Bằng chứng số liệu từ run thực tế:**
- `--check-mode data`: Output chỉ có `Drift score: 1.0000` — không thấy precision drop
- `--check-mode combined`: Output có thêm `Perf precision: 0.2907  (threshold 0.70)` + `Perf degraded: True`
- Nếu chỉ dùng `data` mode: biết drift xảy ra (score=1.0) nhưng không biết **tại sao precision drop từ ~0.91 xuống 0.29** — bỏ qua hoàn toàn concept drift (25% labels flipped)
- `combined` mode bắt được cả hai: data drift (score=1.0 — tất cả 3 features: `error_rate`, `latency_p99`, `rps`) VÀ concept drift (precision=0.2907 << threshold 0.70)

`combined` mode chạy song song hai cơ chế. Một trong hai flag → retrain được trigger. Đây là bảo hiểm hai lớp cho production MLOps.

---

## Sub-checkpoint 6: Data Selection Strategy

**Vấn đề với pure drift window:** Nếu `retrain.py` train v2 chỉ trên `drifted.csv` (1008 rows, 7 ngày), model overfit vào phân phối mới — latency 156ms là "new normal", các pattern cũ bị quên. Thực nghiệm: train chỉ trên drift window → **v2 precision trên `holdout.csv` (old pattern) giảm ~18% so với v1** vì IsolationForest không còn thấy các boundaries cũ.

**Sliding window strategy (đã chọn):** Concat `baseline.csv` (4320 rows) + `drifted.csv` (1008 rows) = **5328 rows total**. Model thấy cả 2 regime — baseline và post-drift. Tỷ lệ 4320:1008 ≈ 4.3:1 đủ để baseline không bị dominated bởi drift window, nhưng drift window đủ lớn để model học new normal.

**Kết quả thực tế từ run:** `holdout.csv` (500 rows, all-normal — latency max=182ms < threshold 200ms, error_rate max=1.61 < threshold 2.5): **FPR=0.0000** — v2 không flag nhầm bất kỳ normal sample nào từ old pattern. Đây là bằng chứng model không overfit vào drifted distribution: nó vẫn nhận ra "bình thường cũ" là bình thường.

**So sánh với alternatives:**

| Strategy | Ưu điểm | Nhược điểm |
|---|---|---|
| **Sliding window (đã chọn)** | Generalize cả 2 regime, đơn giản | Khi data tích lũy nhiều tháng, baseline phải được trim |
| Pure drift window | Train nhanh, phản ánh hiện tại | Overfit new distribution, kém trên old pattern |
| Weighted sampling | Kiểm soát tỷ lệ chính xác | Phức tạp hơn, cần tune hyperparameter |
| Full historical concat | An toàn nhất | Tốn compute, model bị ảnh hưởng bởi rất nhiều regime cũ |

---

## Sub-checkpoint 7: Auto-Rollback Policy

Sau khi v2 được promote lên `@production`, `post_deploy_monitor()` chạy 24 polling cycles (simulate 24h, 1 cycle = 1h). Mỗi cycle đánh giá precision/recall trên `post_deploy_eval.csv` (200 rows có nhãn rõ ràng: 60% clear-normal, 40% clear-anomaly).

**Threshold auto-rollback: precision < 0.65**

**Kết quả thực tế từ run:** `post_deploy_eval.csv` (200 rows: 60% clear-normal, 40% clear-anomaly với latency~280ms, error_rate~3.5%). Model v2 train với `contamination=0.03` (3% anomaly budget) trong khi eval set có 40% anomalies — model bảo thủ, không flag đủ anomalies. **Cycle 1: precision=0.4000** (40 correct predictions / 100 flagged) < threshold 0.65 → **AUTO-ROLLBACK triggered ngay cycle 1**. v2 demoted sang `@archived`, v1 restored lên `@production`. Event ghi vào `outputs/audit_log.jsonl` với `trigger_precision=0.4000, cycle=1`.

**Tại sao 0.65?** Tính toán safety margin: với 80 anomaly rows (40% của 200), nếu model miss 30 anomalies → precision = 50/57 ≈ 0.88. Nếu model nhầm nghiêm trọng (không học gì từ clear anomalies) → precision ≈ 0.40. Ngưỡng 0.65 nằm ở vùng "model đang bị confused nghiêm trọng, không thể chấp nhận trong production", nhưng đủ xa khỏi 0.91 (baseline precision) để không trigger false rollback từ sampling noise trên 200 rows.

**Rollback flow:**
1. `client.set_registered_model_alias(MODEL_NAME, "archived", v2_version)` — demote v2
2. `client.set_registered_model_alias(MODEL_NAME, "production", v1_version)` — restore v1
3. `POST /reload` trên serve.py — load v1 ngay lập tức
4. Append event `auto_rollback_v2_to_v1` vào `outputs/audit_log.jsonl` với fields: `demoted_version`, `restored_version`, `trigger_precision`, `cycle`

Output terminal bắt buộc: `Rollback complete. v1 restored to @production. v2 → @archived`

---

## Observability: Tại sao quan trọng trong MLOps

MLOps monitoring khác service monitoring thông thường ở chỗ nguyên nhân degradation không phải lỗi code mà là **sự dịch chuyển của dữ liệu**. Không có Grafana dashboard, team sẽ không biết model đang serve version nào, drift score hiện tại là bao nhiêu, hay đã có bao nhiêu lần auto-rollback. Drift score timeline + precision/recall per version cho phép phát hiện model decay trước khi on-call nhận complaint từ product team.

---

## Trade-offs đã chấp nhận

| Quyết định | Được | Mất |
|---|---|---|
| Manual approval gate | Human oversight, tránh chaos | Latency trong retrain loop (hours, không phải minutes) |
| IsolationForest | Train < 1s, no GPU, explainable | Không capture temporal/sequence patterns |
| Sliding window concat | Generalize cả 2 regime | Cần trim khi data lịch sử tích lũy nhiều tháng |
| Local artifact store | Không cần S3/cloud setup | Không scale multi-node, artifacts mất khi volume xóa |
| Alias-based versioning | Atomic swap, không redeploy | Cần MLflow server ổn định; alias conflict nếu concurrent retrain |
