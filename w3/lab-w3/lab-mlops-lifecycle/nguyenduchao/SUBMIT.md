# SUBMIT.md — Reflection: MLOps Lifecycle Lab

## Câu 1: Drift threshold bạn chọn là bao nhiêu và tại sao? Có validate không?

Threshold là **0.15** (15% features drifted, tức 1/3 features trong dataset 3 columns). Cơ sở: chạy self-check trên baseline.csv chia 70/30 — noise floor đo được là **0.04** (chỉ có seasonal variation sáng/tối). Threshold 0.15 = 3.75× noise floor — đủ xa để không bị false positive từ intraday traffic fluctuation (±15% latency), nhưng đủ nhạy để bắt drift khi 1-2 features bắt đầu dịch chuyển nghiêm trọng.

Validation thực tế với `drifted.csv`: drift score đo được là **1.0000** (tất cả 3 features đều drifted: `error_rate`, `latency_p99`, `rps` — Wasserstein distance vượt ngưỡng 0.1 trên cả 3). Threshold 0.15 bắt được drift ngay, không bỏ sót. Nếu chọn threshold 0.05 — fire mỗi ngày do daily pattern thay đổi, gây alert fatigue. Nếu chọn 0.50 — bỏ sót giai đoạn đầu khi chỉ 1 feature dịch chuyển, model tiếp tục serve sai mà không ai biết.

---

## Câu 2: Điều gì xảy ra nếu model v2 sau retrain tệ hơn v1?

Pipeline có **3 tầng bảo vệ** để tránh tình huống này. Tầng đầu: holdout validation trong `retrain.py` (`--holdout data/holdout.csv`) — trước khi register staging, v2 được đánh giá trên 500 rows old-pattern data. Nếu `v2 precision < v1 precision` trên holdout, engineer thấy warning và có thể từ chối approve. Tầng hai: **manual approval gate** — ML engineer thấy anomaly_rate và drift report HTML trước khi gõ `y`. Nếu v2 có anomaly_rate quá cao/thấp so với v1, engineer từ chối, v2 ở lại alias `staging` mà không affect production.

Tầng ba: **auto-rollback** sau promote — `post_deploy_monitor()` chạy 24 cycles đánh giá v2 precision trên `post_deploy_eval.csv`. Nếu `precision < 0.65`, v2 bị demote sang `@archived` và v1 được restore lên `@production` tự động trong < 5 giây (chỉ swap alias + gọi `/reload`). Cả 3 tầng được ghi vào `outputs/audit_log.jsonl` để audit sau. Không có tình huống nào khiến v2 tệ hơn v1 mà hệ thống không có cơ chế xử lý.

---

## Câu 3: Sự khác biệt giữa data drift và concept drift? Evidently detect loại nào?

**Data drift**: phân phối input feature thay đổi — **P(X) thay đổi**, nhưng mối quan hệ X→Y giữ nguyên. Ví dụ trong lab: latency baseline tăng từ 120ms lên 156ms sau campaign, rps tăng 40%. Model v1 vẫn đúng về logic nhưng các threshold anomaly không còn phù hợp với new normal. Evidently DataDriftPreset detect chính xác loại này bằng statistical tests (Wasserstein distance cho numerical features) trên từng feature column.

**Concept drift**: mối quan hệ input-output thay đổi — **P(Y|X) thay đổi**. Ví dụ: cùng latency 200ms, trước khi payment processor rollout là anomaly, sau khi rollout thì 200ms là bình thường mới. Feature distribution có thể ổn định nhưng model hoàn toàn sai về label. Evidently DataDriftPreset **không detect được** loại này vì nó chỉ nhìn vào feature values, không biết ground truth label.

Trong lab, `drifted.csv` được inject cả hai loại: data drift (latency +30%, error_rate ×2) và concept drift (25% labels flipped). Chỉ khi dùng `--check-mode combined` mới phát hiện được cả hai. Run thực tế: `--check-mode combined` cho output `Drift score: 1.0000` (cả 3 features drifted, Wasserstein distance: error_rate=2.29, vượt ngưỡng 0.1 rất xa) VÀ `Perf precision: 0.2907` (recall=1.0, nhưng precision cực thấp — model flag gần như tất cả là anomaly vì concept drift làm lệch label). `--check-mode data` alone sẽ báo score=1.0 (đúng về feature shift) nhưng **miss hoàn toàn** precision drop từ 0.91 xuống 0.29 do concept drift.

---

## Câu 4: Tại sao blue-green swap quan trọng hơn replace file trực tiếp?

Replace file trực tiếp (overwrite model artifact trên disk) có ít nhất 3 vấn đề nghiêm trọng. Thứ nhất, **race condition**: serve.py đang xử lý request dùng model cũ, đồng thời file bị ghi đè → corrupted read → crash hoặc wrong prediction trong lúc đang serve production traffic. Thứ hai, **không có rollback**: version cũ đã bị overwrite — nếu v2 tệ hơn, không có gì để restore. Thứ ba, **không có versioning audit**: không biết đang serve version nào tại thời điểm nào.

Blue-green qua MLflow alias giải quyết cả ba. Swap alias là atomic — không có khoảng thời gian trung gian. Serve.py chỉ load model mới khi nhận `POST /reload` — tất cả in-flight requests trước đó hoàn thành an toàn với v1. Cả v1 và v2 đều tồn tại trong registry, không mất artifact nào. Nếu v2 tệ hơn, swap alias về v1 + reload = rollback < 5 giây. `/health/active-version` endpoint cho phép verify đang serve đúng version trước khi cutover traffic 100% — đây là "green check" của blue-green pattern.

---

## Câu 5: Nếu automate approval gate, dùng metric và threshold nào?

Điều kiện auto-promote cần thỏa đồng thời 3 tiêu chí để đảm bảo an toàn trong payment domain:

**Tiêu chí 1 — Holdout anomaly rate delta:** `abs(v2_anomaly_rate - v1_anomaly_rate) < 0.05`. Đo trên 20% cuối của current window làm holdout. Delta 5% trên 1000 requests/phút = 50 missed anomalies/phút — đây là upper bound chấp nhận được trước khi rõ ràng có vấn đề.

**Tiêu chí 2 — Sanity bounds:** `0.01 < v2_anomaly_rate < 0.10`. Anomaly rate < 1% nghĩa là model quá conservative (bỏ sót quá nhiều). Anomaly rate > 10% nghĩa là model degenerate (flag gần như mọi thứ). Cả hai đều là degenerate cases không nên promote.

**Tiêu chí 3 — Drift check trên training data:** Drift score giữa combined training set (baseline + drift window) và validation holdout phải < 0.15. Đảm bảo v2 không được train trên distribution quá khác với gì nó sẽ gặp trong production ngay sau promote.

Nếu cả 3 thỏa → auto-promote + ghi audit log. Nếu không → push alert cho ML engineer review trong 4h, sau 24h không review thì archive staging version và reset. Trong payment domain, 4h SLA cho human review là trade-off hợp lý giữa agility và safety.
