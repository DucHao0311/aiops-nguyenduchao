# SUBMIT — W2-D2 RCA Pipeline

**Name:** nguyenduchao  
**Path:** aiops-nguyenduchao/w2/d2/  
**Branch:** main

---

## EOD Checkpoint — 3 câu trả lời thực tế

### Câu 1: Confidence top-1 và ngưỡng auto-rollback

Cluster lớn nhất tôi xử lý là **c-001-000** (19 alerts, 6 services, severity=crit).

Confidence của top-1 sau LLM enrichment: **0.56** (kNN base: 0.53, LLM-adjusted: 0.56).

Tôi sẽ đặt threshold auto-rollback ở **0.82**. Lý do cụ thể từ những gì tôi đã thấy:

Pipeline của tôi trả ra `checkout-svc` là root cause với score 0.647 — nhưng khi nhìn vào timeline alerts thực tế, `payment-svc` alert xuất hiện trước (09:42:01Z) với fingerprint `db_connection_pool_used_ratio`. `checkout-svc` alert (09:42:45Z) là downstream victim. Với confidence 0.56, pipeline đã sai root cause. Nếu tôi auto-rollback `checkout-svc` theo output này, vấn đề thực sự (payment pool exhaustion) vẫn còn đó.

0.82 là ngưỡng tôi chọn vì: (1) nó tương đương với similarity score mà pipeline sẽ assign khi `payment-svc` được retrieve đúng với `INC-2025-11-08` (connection_pool_exhaustion scenario), (2) tránh được false positive rollback trên critical revenue path, (3) trong 2 cluster tôi có — cluster c-002-001 với recommender-svc (low criticality) tôi có thể accept threshold thấp hơn (0.65) vì rollback recommender không block checkout flow.

---

### Câu 2: Classifier variant và trade-off

Tôi chọn **Variant A — rule-based kNN retrieval** (không dùng LLM API, không dùng paid service).

**Chạy thực tế:** kNN keyword LOO accuracy = 6.9%, TF-IDF LOO = 17.2%. Decision Tree CV = 0% (fail vì 30 incidents / 17 unique classes — sparse). Cả 3 method đều predict `ddos` cho cluster c-001-000, nhưng ground truth là `connection_pool_exhaustion`. Pipeline đã retrieve INC-2026-03-20 (DDoS) là top-1 vì service overlap (edge-lb + checkout-svc + payment-svc) khớp — nhưng mechanism hoàn toàn khác.

**Trade-off với variant B (free LLM — Groq):** Groq/LLaMA có thể đọc fingerprint `db_connection_pool_used_ratio|crit` và trực tiếp map sang `connection_pool_exhaustion` mà không cần semantic match với historical incidents. Nó cũng hiểu rằng "downstream_payment_error_rate" trên checkout-svc là symptom, không phải cause. Nhược điểm: phụ thuộc external API (latency, rate limit, uptime), không reproducible 100%, không chạy được offline.

Rule-based tôi chọn vì: predictable, fast, no API cost, và đủ tốt như baseline. Nhưng tôi thừa nhận nó thất bại ở case này vì keyword similarity không phân biệt được "DDoS gây edge-lb saturate" vs "pool exhaustion gây payment-svc cascade" khi services overlap.

---

### Câu 3: So sánh với Industry landscape

Pipeline tôi xây **gần Dynatrace Davis nhất** — cả 2 đều lấy topology (service graph) làm source of truth, traverse ngược từ alert đến origin qua dependency edges.

Trong domain **GeekShop** (e-commerce, alert volume cao, service map tương đối ổn định), lựa chọn này **hợp lý** vì:
- Service map của GeekShop ổn định (14 nodes, 17 edges, không thay đổi thường xuyên) — đây là điều kiện tiên quyết để Dynatrace-style approach hoạt động tốt.
- Alert volume cao → cần graph để nhanh chóng prune candidate space, thay vì scan tất cả services.
- Revenue critical path rõ ràng (edge-lb → checkout-svc → payment-svc → payments-db) — graph propagation model capture được điều này.

Tuy nhiên, có một điểm yếu rõ ràng tôi đã thấy: khi graph thiếu tier/criticality weighting đủ mạnh, edge-lb (in-degree=0) bị rank quá cao dù là downstream victim. Pipeline cần thêm `metric_type` signal (db_connection_pool > latency > cpu) để không bị confused bởi cascade propagation.

**Không nên đổi sang Causely** vì Causely cần time-series dài để học causal graph — với setup GeekShop hiện tại (service map đã có sẵn, ổn định), đó là overhead không cần thiết. Graph-based approach là shortcut đúng cho domain này.
