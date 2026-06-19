# DESIGN.md — Ronki Closed-Loop Orchestrator (nguyenduchao)

## 1. Decision Engine: Rule-based hay LLM-based?

**Lựa chọn: Option A — Rule-based.**

### Lý do

Stack Ronki định nghĩa chính xác 3 loại alert: `HighLatency`, `HighErrorRate`, `InstanceDown`. Mỗi alert có nguyên nhân đã biết và action đã được ops team kiểm chứng qua nhiều incident. Trong bối cảnh này:

- **Determinism là bắt buộc**: cùng alert phải luôn trigger cùng runbook, không phụ thuộc vào context window hay temperature của model.
- **Latency quyết định < 1ms**: quan trọng trong production khi window từ alert fire đến action phải tối thiểu hóa.
- **Không có API dependency**: rule-based hoạt động offline hoàn toàn, không bị gián đoạn khi Anthropic API down hoặc rate-limited.
- **Dễ audit**: mỗi quyết định trả về `alertname → runbook` — một dòng config, không có black-box reasoning.

### Trade-off

| Tiêu chí | Rule-based (Option A) | LLM-based (Option B) |
|---|---|---|
| Latency quyết định | < 1ms | 200–800ms (API round-trip) |
| Determinism | 100% | Phụ thuộc temperature, prompt version |
| Mở rộng alert mới | Cần cập nhật `runbook_map` thủ công | Tự suy luận từ alert description |
| Chi phí vận hành | $0 | ~$0.003–0.01/quyết định |
| Xử lý alert phức tạp | Không (map 1-1 chỉ) | Có thể reason cross-signal |
| Fallback khi offline | N/A | Cần rule-based fallback |
| Hallucination risk | Không (static map) | Có — cần `runbook_registry` validation |
| Audit trail | Trivial (key lookup) | Cần log thêm prompt + response |

**Kết luận**: với 3 alert type cố định và yêu cầu reliability cao trong production, rule-based là lựa chọn đúng. Khi hệ thống mở rộng lên 20+ alert type với mô tả tự nhiên, hoặc cần cross-signal correlation (e.g., correlate latency spike với deployment event), LLM-based với `confidence >= 0.6` và rule-based fallback sẽ được xem xét bổ sung.

---

## 2. Blast-radius Config

```yaml
blast_radius:
  max_actions_per_minute: 3
  max_restarts_per_service_per_hour: 5
```

### Lý do chọn giá trị

**`max_actions_per_minute: 3`**

Stack có 5 service. Trong cascade failure điển hình (payment-svc down → checkout-svc timeout → api-gateway error rate tăng), Alertmanager có thể fire 3–5 alert cùng lúc. Giới hạn 3 action/phút:
- Cho phép xử lý 3 service trong 1 phút đầu — đủ để xử lý cascade failures phổ biến nhất
- Tránh **thundering herd**: nếu orchestrator restart đồng loạt 5 service cùng lúc, database connection pool sẽ bị exhausted khi tất cả container reconnect cùng thời điểm
- Các alert không được xử lý sẽ log `BLAST_RADIUS_EXCEEDED` và vẫn tồn tại trong Alertmanager để retry chu kỳ sau (15s)

**`max_restarts_per_service_per_hour: 5`**

Nếu một service bị restart > 5 lần trong 1 giờ mà vẫn fail, đây là dấu hiệu rõ ràng của lỗi không tự phục hồi:
- **OOM loop**: container bị kernel OOM-kill ngay sau khi start, không phải transient
- **Config sai**: app crash on startup do env var thiếu hoặc sai
- **Dependency down**: database/external API không reachable — restart vô ích

5 lần = đủ để xử lý transient failures (brief network blip, slow cold start sau deployment) trước khi escalate. Khi vượt ngưỡng, orchestrator log `BLAST_RADIUS_EXCEEDED`, alert tiếp tục firing cho đến khi human can thiệp.

---

## 3. Verify Step

### Metric kiểm tra

Verify step kiểm tra **đồng thời** hai metric từ `baseline.json`:

1. **`latency_p99`** — p99 request latency (ms) qua PromQL:
   ```promql
   histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="{service}"}[1m])) * 1000
   ```

2. **`up`** — service reachability qua PromQL:
   ```promql
   up{job="{service}"}
   ```

Cả hai phải pass đồng thời. Nếu `up == 0`, latency check không có ý nghĩa.

### Threshold

Từ `baseline.json`, p99 bình thường dao động 72–230ms (inventory-svc: 72ms, checkout-svc: 230ms).

- **`latency_p99_max_ms: 500`** — ~2× baseline p99 của service chậm nhất (checkout-svc: 230ms). Đủ rộng để tránh false negative (service mới restart chưa warm up JIT/connection pool) nhưng đủ chặt để phát hiện nếu action không có hiệu quả.
- **`up_required: 1`** — service phải reachable hoàn toàn.

### Timeout và polling config

```yaml
verify_timeout_seconds: 60
verify_poll_interval_seconds: 10
verify_min_samples: 3
```

**Lý do từng giá trị:**

- **`60s timeout`**: container restart mất 3–5s, sau đó cần 10–20s để Prometheus scrape data mới (scrape interval 10s). 60s = 5–6 scrape cycle — đủ thời gian để metric ổn định sau restart. Nếu sau 60s service vẫn unhealthy, action thực sự không có hiệu quả.

- **`10s poll_interval`**: match chính xác với Prometheus scrape interval — không có lợi ích khi poll nhanh hơn data refresh rate. Poll nhanh hơn chỉ tăng noise.

- **`3 min_samples consecutive`**: tránh false positive từ 1 sample may mắn. Yêu cầu 3 mẫu liên tiếp (`consecutive`, không phải tổng cộng) đảm bảo service thực sự ổn định. Nếu có 1 sample unhealthy xen giữa, counter reset về 0 — phải healthy liên tục mới pass.

**Thực tế**: với 10s poll interval và 3 min_samples, verify thành công sau tối thiểu 20s (3 × poll) và tối đa 60s. Đủ nhanh để không kéo dài incident, đủ chặt để không false positive.

---

## 4. Circuit Breaker Reset

**Reset mode: `manual`** — khởi động lại orchestrator process để reset.

### Lý do chọn manual reset

Circuit breaker mở sau 3 consecutive verify failures — trạng thái nghiêm trọng cho thấy:
- Orchestrator đã thực hiện 3 action liên tiếp và không có action nào restore service thành công
- Có thể là: runbook bị lỗi, infrastructure failure lan rộng, hoặc root cause không liên quan đến service layer

**Tại sao KHÔNG dùng automatic reset (timer-based)**:
- Orchestrator resume sau N phút mà không biết root cause đã được fix chưa
- Nguy cơ infinite loop: action → fail → reset timer → action → fail → ...
- Mỗi cycle fail có thể làm tình trạng tệ hơn: thundering herd, DB connection pool exhaustion, data inconsistency
- Automation không có đủ signal để tự phán đoán "root cause đã được giải quyết"

**Manual reset đảm bảo**:
- Kỹ sư buộc phải xem `audit_log.jsonl` để tìm pattern
- Xác nhận root cause thực sự đã được fix
- Chủ động restart orchestrator khi đã sẵn sàng

**Cách reset trong thực tế**:
```bash
# 1. Ctrl+C dừng orchestrator
# 2. Diagnose từ audit log
cat audit_log.jsonl | python -c "import sys,json; [print(json.dumps(r)) for line in sys.stdin for r in [json.loads(line)] if r.get('event_type') in ['VERIFY_FAIL','ROLLBACK_TRIGGERED','CIRCUIT_BREAKER_HALT']]"
# 3. Fix root cause
# 4. Restart
uv run python closed_loop.py --config config.yaml
```

**Extension nếu cần auto-reset**: thêm `cool_down_seconds: 1800` (30 phút) kết hợp với alert riêng notify on-call khi circuit mở. Sau 30 phút, auto-reset một lần — nếu lại fail → permanent halt cho đến manual reset. Nhưng 30 phút downtime window đủ cho on-call xác định issue trong production bình thường.

---

## 5. Per-service Mutex (Stress #2 — Concurrent Alert Race)

**Thiết kế**: dict `_service_locks` map `service_name → threading.Lock()`, được bảo vệ bởi `_locks_meta` lock khi khởi tạo. Mỗi service có lock độc lập.

**`acquire(blocking=False)`**: nếu service đang có runbook chạy → log `SERVICE_LOCK_BUSY` và skip alert duplicate. Không queue, không wait.

**Lý do `blocking=False` thay vì queue**:
- Trong 30s runbook đang chạy trên service A, alert tiếp theo từ cùng service A là duplicate của incident chưa resolved
- Queue sẽ khiến runbook chạy lại ngay sau khi lock release — restart service 2 lần liên tiếp — nguy hiểm hơn skip
- Alert vẫn còn trong Alertmanager, sẽ được pick up trong poll cycle tiếp theo nếu vấn đề chưa resolved

**Concurrency của 2 service khác nhau**: vì mỗi service có lock riêng, `payment-svc` và `inventory-svc` acquire lock độc lập — chạy song song trong separate threads không block nhau. Đã verify: cả 2 service log `DRY_RUN_PASS` cách nhau < 1ms (timestamps 0.006s và 0.007s).

---

## 6. Transactional Multi-step Rollback (Stress #1)

**Thiết kế**: `run_transactional_steps()` thực thi steps A→B→C và tích lũy `completed` list. Khi step C fail:
1. Lấy `rollback_steps[:len(completed)]` — chỉ rollback steps đã thực hiện (không rollback step chưa chạy)
2. Duyệt `reversed(steps_to_rollback)` — rollback B trước A (LIFO)

**Lý do LIFO là đúng về kỹ thuật**:
- Step A (drain traffic) tạo state mà Step B (apply config) phụ thuộc vào
- Nếu rollback A trước B: service nhận traffic trong khi config đang ở trạng thái inconsistent
- LIFO đảm bảo teardown đi ngược setup — cùng nguyên lý với database transaction rollback và Terraform destroy

**Log observable**: `TRANSACTIONAL_ROLLBACK_STEP` xuất hiện đúng 2 lần theo thứ tự rollback-B → rollback-A, sau đó `TRANSACTIONAL_ROLLBACK_COMPLETE` với `rolled_back=[rollback-B, rollback-A]`.

---

## 7. Decision Validation (Stress #3 — LLM Hallucination Defence)

**Thiết kế**: trước dry-run, `validate_runbook()` kiểm tra tên runbook có trong `runbook_registry` (whitelist tường minh trong `config.yaml`).

**Flow khi validation fail**:
```
ALERT_DETECTED → DECIDE_RUNBOOK → DECISION_VALIDATION_FAILED → return
```
Không có `RUNBOOK_EXEC`, không có subprocess spawned, circuit breaker counter **không** tăng.

**Lý do cần whitelist tường minh**:
- LLM có thể trả về tên hợp lý về ngôn ngữ nhưng không tồn tại: `scale_down_database.sh`, `kill_all_pods.sh`
- Nếu không validate: bash subprocess với path không tồn tại → exit non-zero → increment circuit breaker → 3 hallucinations → circuit OPEN → toàn bộ automation dừng
- Validation trước dry-run ngăn chặn chuỗi đó

**Distinction quan trọng**: `DECISION_VALIDATION_FAILED` ≠ action failure. Circuit breaker chỉ count verify failures sau khi action đã thực sự được execute. Validation failure chỉ log audit event và escalate.

---

## 8. Structured Log Schema

Mọi event trong `audit_log.jsonl` tuân theo schema:

```json
{
  "ts": "2026-06-19T06:40:42.632584+00:00",
  "level": "INFO|WARNING|ERROR",
  "event_type": "ALERT_DETECTED|DRY_RUN_PASS|ACTION_EXECUTED|...",
  "logger": "orchestrator|safety|verify",
  "service": "payment-svc",
  "action": "...",
  "result": "..."
}
```

`event_type` values được dùng trong `expected.json` làm acceptance criteria. Log được ghi đồng thời ra stdout (cho monitoring) và `audit_log.jsonl` (cho Promtail → Loki).
