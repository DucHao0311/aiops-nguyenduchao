# DESIGN.md — W3-D1 SLO & Burn-Rate Alerting

> **Tham chiếu dữ liệu:** `baseline.json` (3-day window, 2 073 780 API events, 172 639 DB queries, 518 400 RUM events) và `validation_report.json` (verdict: pass, noise_reduction 86.4%, mttd_delta 60 s, fn 0).

---

## Câu 1 — SLI Choice cho Frontend

### Tại sao chọn Composite Availability (dom_ready + js_error + network_error)?

Frontend RUM cung cấp 4 tín hiệu: **page load time (dom_ready_ms)**, **DOM ready time**, **JS error rate**, và **network error rate**. Sau khi phân tích `frontend_rum.jsonl` với baseline `dom_ready_p99 = 1 430 ms` và `fail_rate = 1.39%`, tôi chọn **composite availability**: `count(dom_ready_ms < 3 000 AND js_error = false AND network_error = false) / count(all)`.

**Loại JS error rate (standalone):** JS error rate đơn độc không proportional với user pain — một lỗi `TypeError` trong analytics script không làm người dùng không checkout được, trong khi một lỗi JS trong payment flow hoàn toàn block user. Dùng riêng js_error làm SLI sẽ vi phạm tiêu chí "proportional" (Google SRE Workbook §2.1).

**Loại network error rate (standalone):** Network error rate thường bị ảnh hưởng bởi CDN prefetch, beacon request bị cancel — những sự kiện này không gây user pain trực tiếp. Baseline ghi nhận `network_error_rate ≈ 0.69%` ngay cả trong điều kiện bình thường do browser behavior, khiến nó không phải "measurable with clean signal."

**Loại page load time (tuyệt đối):** `dom_ready_p99 = 1 430 ms` trong baseline — đây là giá trị cao và biến động theo thiết bị/mạng người dùng. Dùng mốc cứng (ví dụ > 3s) sẽ bao phủ đuôi phân phối bình thường của mobile user, tạo false positive. Tuy nhiên, `dom_ready_ms >= 3 000` có giá trị trong composite vì nó đại diện cho trải nghiệm tệ rõ ràng.

**Lý do giữ composite:** Kết hợp cả 3 điều kiện (`dom_ready < 3 000 AND no_js_error AND no_network_error`) cho phép SLI phản ánh **toàn bộ user journey** — trang load nhanh, không crash JS, không mất kết nối. `baseline.json` ghi nhận `success_rate = 98.61%` với composite này, tức baseline hiện tại đã đạt trên SLO target 99% chỉ khoảng 0.39 pp — có buffer hợp lý để alert.

---

## Câu 2 — SLO Target cho API: Tại sao 99.9%?

### Baseline hiện tại và lựa chọn target

`baseline.json` ghi nhận `api.success_rate = 97.63%` (3-day window bao gồm 3 incident). Tuy nhiên, success rate này tính cả latency threshold — nếu chỉ đếm availability thuần (không bao gồm latency), `fail_rate = 0.298% (5xx) + 0.051% (429) = 0.35%`, tương đương **availability 99.65%** trong điều kiện bình thường có incident. Không có incident, ước tính baseline availability ≈ **99.9% – 99.95%**.

**Tại sao không chọn 99%?** SLO 99% cho phép 7h 18m downtime/tháng — quá lỏng cho e-commerce API xử lý orders và checkout. Incident #1 (8 phút, fail\_rate\_multiplier=100) đã consume ~19% budget của 99% target chỉ trong một sự cố. Hơn nữa, SLA với customer thường được set ở 99%, vậy SLO phải chặt hơn để có buffer.

**Tại sao không chọn 99.99%?** SLO 99.99% chỉ cho phép 4m 19s downtime/tháng. Incident #1 (8 phút total outage) đã vi phạm ngay tháng đầu — không feasible với kiến trúc hiện tại (4 FastAPI instances, không có multi-AZ failover rõ ràng). Cost ladder (Google SRE Chapter 4) cho thấy 99.99% đòi hỏi multi-AZ automated runbook + 24/7 on-call — chi phí infra+ops tăng 3–10× so với 99.9%.

**Kết luận:** 99.9% là điểm cân bằng giữa baseline measurement (99.65% với incident, ~99.9% không có incident) và cost constraint của stack FastAPI 4-instance. Budget 20 738 failures/month cho phép absorb incident #1 (ước tính ~5 000 failures trong 8 phút × ~10 req/s) mà không exhaust budget.

---

## Câu 3 — Latency Threshold p99: Tại sao 500 ms?

### Phân phối latency 3-day từ access_log.jsonl

| Percentile | Latency (ms) |
|-----------|-------------|
| p50       | 45           |
| p90       | 86           |
| p95       | 104          |
| p99       | 156          |
| p99.9     | 394          |

**Tại sao cut ở 500 ms thay vì 200 ms?** p99 baseline = 156 ms, p99.9 = 394 ms. Nếu cut ở 200 ms, khoảng ~0.5% traffic bình thường sẽ fail SLI ngay khi không có incident — SLI sẽ báo "degraded" trong điều kiện normal operation, vi phạm nguyên tắc proportional. Theo Google Web Vitals, ngưỡng "Good" cho API response là < 200 ms cho core interaction, nhưng **500 ms** là ngưỡng "needs improvement" — đây là ranh giới user-noticeable delay (Doherty Threshold ~400 ms).

**Tại sao không cut ở 1 000 ms?** 1 s quá lỏng. Trong incident #1 (fail\_rate\_multiplier = 100, latency tăng ≈ 30×), latency ước tính lên > 5 000 ms — cut ở 1 000 ms vẫn detect được. Tuy nhiên, cut ở 500 ms vừa đủ tight để phát hiện degradation sớm (~3× p99 baseline) mà không gây false positive trong normal operation.

**Tại sao p99 thay vì p50?** p50 = 45 ms không capture tail latency — 1% user chịu > 156 ms không phản ánh trong p50. Đối với e-commerce (checkout, order), tail latency p99 là chỉ số user pain thực sự. Google SRE Workbook recommends p99 làm default cho user-facing services (§2.3).

**Kết luận:** Latency SLI threshold = **500 ms tại p99** — đây là ~3.2× p99 baseline (156 ms), đủ buffer cho normal variance nhưng chắc chắn fail khi có incident degradation nghiêm trọng.

---

## Câu 4 — 4xx Exclusion: Tại sao loại 4xx ra khỏi error count?

### Nguyên tắc

HTTP 4xx (không bao gồm 429) là **client-side error** — server đã xử lý đúng request và trả về response hợp lệ. Lỗi 400 (bad request), 401 (unauthorized), 403 (forbidden), 404 (not found) đều là hành vi của user/client, không phải lỗi của hệ thống. Đếm vào fail sẽ khiến SLI bị kéo xuống bởi bot, scraper, hay user nhập sai URL — những sự kiện không phản ánh system health.

**429 là ngoại lệ:** 429 (Too Many Requests) xảy ra khi **hệ thống** rate-limit user — đây là system-side decision reject request, không phải user error. Từ góc độ user, request của họ bị từ chối bởi system → user pain = system responsible.

### Phân tích data thực tế

Từ `access_log.jsonl` (3-day, 2 073 780 requests):

| Metric | Count | Rate |
|--------|-------|------|
| 4xx (not 429) | 41 712 | **2.01%** |
| 5xx | 6 185 | 0.30% |
| 429 | 1 049 | 0.05% |

**4xx rate 2.01% trên mọi endpoint** — uniform distribution qua 5 endpoints (`/api/cart`, `/api/products`, `/api/orders`, `/api/checkout`, `/api/user` đều 1.98–2.04%). Đây là tỷ lệ **bình thường cho e-commerce API** — các request 400/401/403/404 đến từ:
- Bot traffic scan endpoint (404)
- Session expired gọi authenticated API (401/403)
- Malformed form submission (400)

Nếu đếm 4xx vào fail, SLI sẽ là `(6185 + 1049 + 41712) / 2073780 = 2.36%` — tức SLO 99% cũng **không bao giờ đạt được** trong điều kiện bình thường. Không có endpoint nào có 4xx > 5% do *hệ thống lỗi* — 2% là baseline background noise của client-side error. Việc loại 4xx (không 429) khỏi error count giúp SLI đo đúng **system reliability**, không phải **client quality**.

---

## Câu 5 — MWMBR Tuning: Google Default hay Tune?

### Quyết định: Giữ Google Default (14.4, 6, 1)

Sau khi chạy `validate.py` với Google SRE default thresholds (14.4 cho Tier 1, 6 cho Tier 2, 1 cho Tier 3), kết quả là:

| Metric | Static Baseline | MWMBR (ours) |
|--------|----------------|--------------|
| Fired  | 22 | 3 |
| TP     | 3  | 3 |
| FP     | 19 | 0 |
| FN     | 0  | 0 |
| MTTD p50 | 0 s | 60 s |
| Noise reduction | — | **86.4%** |

**Tại sao không tune xuống (ví dụ threshold 10)?** Giảm threshold sẽ tăng sensitivity — alert fire sớm hơn nhưng có thể tăng FP. Với threshold 14.4, hiện tại FP = 0, FN = 0 — đây là kết quả lý tưởng. Giảm threshold không cải thiện recall (đã 100%) mà chỉ có nguy cơ tăng FP.

**Tại sao không tune lên (ví dụ threshold 20)?** Tăng threshold sẽ bỏ lỡ incident có burn rate vừa phải (6 ≤ BR < 20). Incident #5 (api, tier2, 20 phút, multiplier=10) có burn rate ≈ 10 — nếu threshold là 20, Tier 1 sẽ miss incident này, tăng FN.

**Về window selection:** Tỷ lệ short/long ≈ 1/12 (Google SRE Workbook §7.1) được giữ nguyên: `(5m / 60m = 1/12)` cho Tier 1, `(30m / 360m = 1/12)` cho Tier 2. Short window đảm bảo alert recover nhanh sau khi sự cố hết (~5 phút), tránh alert "dính" hàng giờ. `mttd_delta = 60 s` so với static baseline là acceptable trade-off: tăng 60 giây MTTD để đổi lấy 86.4% noise reduction (giảm từ 22 xuống 3 fires, FP từ 19 xuống 0).

**Kết luận:** Google default MWMBR (14.4 / 6 / 1) phù hợp với data của lab này. Không cần tune vì `noise_reduction = 86.4% ≥ 70%`, `mttd_delta = 60 s ≤ 60 s`, và `fn = 0` — đạt acceptance criteria theo §9.5.
