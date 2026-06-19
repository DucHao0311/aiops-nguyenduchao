# W3-D2 Submission — Nguyen Duc Hao

## 3 thứ tôi học được về AIOps pipeline của mình

1. **Metric layer không đủ để cover toàn bộ fault class.** Experiment 6 (auth clock skew) dạy tôi rằng pipeline hoàn toàn mù với semantic fault — lỗi JWT, clock drift, cert expiry — vì chúng manifest ở HTTP 4xx (hoặc thậm chí không có HTTP signal), không phải 5xx. Detector chỉ nhìn thấy những gì Prometheus scrape được; bất kỳ fault nào không làm thay đổi HTTP performance metrics đều invisible. Đây là §7.1 failure mode (anomaly chìm dưới noise floor) nhưng cực đoan hơn: không có signal nào cả, không phải signal yếu.

2. **Topology depth là quyết định sống còn của RCA, nhưng infrastructure services cần được đặt ở depth đặc biệt.** Experiment 9 (DNS slow) cho thấy: nếu một infrastructure service (dns-resolver) được model như child của app service (api-gateway) trong topology graph, RCA sẽ sai hướng hoàn toàn. DNS là "upstream" theo nghĩa infrastructure dependency, không phải theo nghĩa request flow — hai khái niệm "upstream" này conflict nhau trong một flat topology graph. Pipeline cần phân biệt *call graph* (request flows) với *dependency graph* (infrastructure dependencies).

3. **External synthetic probe là signal đáng tin cậy nhất và không thể bị thay thế bởi internal metrics.** Trong 8/10 experiments được detected, probe.log xác nhận user impact rõ ràng và sớm hơn hoặc đồng thời với Prometheus alerts. Đặc biệt exp 8 (full partition): probe ngay lập tức cho thấy `fail 000` trong khi Prometheus scrape cũng chết — nếu không có external probe, không có signal nào để confirm detection. Probe là ground truth cho §6.1 confusion matrix.

---

## 1 fault mà tôi mong pipeline catch nhưng nó miss

**Experiment:** 6 — `auth_svc_clock_skew`

**Why I expected detection:** Clock skew +60s trên auth-svc sẽ làm JWT tokens có `exp` claim nhỏ hơn current time từ góc nhìn của auth-svc, dẫn đến từ chối tất cả requests như "token expired". Trong hệ thống real, điều này tạo ra làn sóng 401 Unauthorized responses visible qua error rate metrics. Alert rule HighErrorRate (>10%) nên fire trong vòng 20-30s.

**Why pipeline missed (hypothesis):** Hai lý do độc lập cộng hưởng: (1) Mock service.py không implement JWT validation — nó chỉ giả lập latency và random 5xx theo FAIL_RATE, không có JWT logic, nên clock skew hoàn toàn không có tác dụng lên service behavior. (2) Alert rules chỉ count `status=~'5..'` errors, không count 4xx — ngay cả khi có JWT logic thật, 401 responses sẽ không trigger HighErrorRate alert. Cần thêm: (a) JWT-aware mock logic, (b) alert rule cho auth failure rate (4xx/total > threshold), (c) hoặc `node_timex_offset_seconds` scrape để detect clock drift trực tiếp.

---

## 1 trade-off trong design pipeline mà tôi muốn rethink

**Trade-off hiện tại:** Topology-aware RCA sử dụng *call graph depth* làm proxy duy nhất để xác định "upstream" (root cause) vs "downstream" (symptom). Điều này hoạt động tốt cho 8/10 experiments nhưng fail với infrastructure services (DNS, cache, database) — những service mà request flow không đi "qua" chúng theo nghĩa trực tiếp, nhưng chúng lại là hard dependencies của toàn bộ stack.

**Vấn đề cụ thể:** Nếu dns-resolver fail, api-gateway sẽ có nhiều alerts hơn dns-resolver (vì api-gateway là điểm tập trung của tất cả DNS failures từ tất cả services). Topology depth của api-gateway (1) thấp hơn hoặc bằng dns-resolver (1), nên RCA pick api-gateway — sai.

**Muốn rethink thành gì:** Hybrid RCA model gồm 2 layers: (1) **Call graph layer** cho app-to-app dependencies (checkout → payment), dùng depth như hiện tại; (2) **Infrastructure dependency layer** cho DNS, cache, DB — modeled riêng, luôn được RCA ưu tiên nếu alerting, bất kể call graph depth. Điều này gần với model của Netflix Vizceral và Google SRE "dependency tiers" — phân biệt *synchronous call dependencies* (RPC/HTTP) với *shared infrastructure dependencies* (DNS, secret store, config service).

---

## Scoreboard Summary

| Metric | Value |
|--------|-------|
| detected | 9/10 |
| rca_correct | 8/9 |
| mttd_p50 | 36s |
| mttd_p95 | 57s |
| false_alarms | 0 |
| precision | 1.00 |
| recall | 0.90 |
| verdict | **PASS ✓** |

**Acceptance thresholds met:**
- detect ≥ 7/10: ✓ (9/10 = 90%)
- RCA correct ≥ 5/detected: ✓ (8/9 = 89%)
- false alarms ≤ 1: ✓ (0)

**Top gap:** Semantic fault detection (clock skew / auth failures) — requires 4xx monitoring + JWT-aware service mocks.  
**Runner-up gap:** Infrastructure service RCA — DNS, cache, DB must be classified separately from app-tier call graph to avoid §7.3 "pick loudest downstream" error.
