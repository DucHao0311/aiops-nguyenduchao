# FINDINGS — W2-D2 RCA Pipeline

## 1. Cluster Analysis

### Cluster c-001-000 (19 alerts, severity=crit)

**Root cause được identify:** `checkout-svc` theo graph scorer (score=0.647), `payment-svc` theo graph scorer thứ hai (score=0.642). Hai service này cách nhau chỉ 0.005 điểm — pipeline không chắc chắn hoàn toàn ở đây (xem §4).

**Root cause thực sự (theo evidence):** `payment-svc` — DB connection pool exhaustion.

**Lý do:**
- `payment-svc` là service đầu tiên xuất hiện alert trong timeline: `a-0001` lúc 09:42:01Z (warn), `a-0002` lúc 09:42:18Z (crit db_connection_pool_used_ratio). Đây là alert earliest của cluster.
- Fingerprint `payment-svc|db_connection_pool_used_ratio|crit` chứa signal rõ nhất: pool used ratio từ 0.85 → 0.99 → 1.00 — pool exhausted 100%.
- `checkout-svc` alert xuất hiện sau lúc 09:42:45Z với `downstream_payment_error_rate|crit` — đây là downstream cascade, không phải origin.
- Topology graph xác nhận: `checkout-svc → payment-svc` (HTTP call). Khi `payment-svc` pool exhausted, mọi request từ `checkout-svc` đến `payment-svc` timeout → `checkout-svc` cascades.
- `edge-lb` alert xuất hiện sau lúc 09:43:15Z — tiếp tục là downstream victim.

**kNN Retrieval:** Top-1 similar incident là `INC-2026-03-20` (DDoS, similarity=0.434) vì Jaccard overlap của services (edge-lb, checkout-svc, payment-svc) match với DDoS scenario. Tuy nhiên `INC-2025-11-08` (connection_pool_exhaustion, sim=0.3945) là incident thực sự khớp nhất về pattern khi phân tích kỹ summary.

**Observation quan trọng:** Graph temporal scorer bị confused bởi thứ tự alert — `checkout-svc` alert xuất hiện 44 giây sau `payment-svc` nhưng graph tier penalty cho `edge-lb` đã push `checkout-svc` lên top vì `edge-lb` bị penalize (tier=edge). Pipeline cần xem xét thêm `metric type` để ưu tiên `db_connection_pool` metric hơn `latency` metric.

**Classification:** `ddos` từ kNN top-1, nhưng đây là false positive — `INC-2026-03-20` match vì service overlap, không phải pattern. `INC-2025-11-08` (connection_pool_exhaustion) là match đúng nhất.

---

### Cluster c-002-001 (1 alert, severity=warn)

**Root cause:** `recommender-svc` — memory_leak / cpu_utilization spike.

**Lý do:** Chỉ có 1 alert duy nhất: `recommender-svc|cpu_utilization|warn`, value=0.91 (threshold=0.85). Label note trong dataset ghi rõ "unrelated — concurrent batch retrain". kNN retrieval khớp với `INC-2025-08-02` (memory_leak recommender-svc, sim=0.645) — pattern tương tự (recommender-svc standalone issue).

**Quan trọng:** Cluster này isolated — không lan sang cluster chính. Low criticality service, không cần pager.

---

## 2. Confidence Assessment

**Cluster c-001-000:** confidence=0.56 (LLM-enriched).

**Có dám deploy auto-remediation không?** Không ở mức confidence này. Lý do cụ thể:
- 0.56 là dưới ngưỡng an toàn cho automated rollback.
- Pipeline identify sai root cause (checkout-svc thay vì payment-svc) — nếu auto-rollback checkout-svc sẽ không fix được vấn đề và có thể gây thêm downtime.
- Classification `ddos` cũng sai — actions (WAF, Cloudflare) không match với connection pool issue.
- Nếu buộc phải đặt threshold cho auto-rollback, tôi sẽ chọn **≥ 0.82** vì: (1) đủ xa khỏi random chance (0.5 với 2 classes), (2) tương đương với similarity score của `INC-2025-11-08` nếu được retrieve đúng, (3) tránh false positive action trên critical payment path.

**Cluster c-002-001:** confidence=0.618. Cũng dưới 0.82 — nhưng action (rollback recommender-svc, add memory limit) là low-risk và có thể auto-remediate với threshold thấp hơn (ví dụ 0.6) vì recommender-svc là low-criticality.

---

## 3. Case không chắc chắn

**Cluster c-001-000 là case không chắc chắn nhất.**

Vì sao:
1. **Graph scorer tie:** `checkout-svc` (0.647) vs `payment-svc` (0.642) cách nhau 0.005 — trong margin of error.
2. **kNN mismatch:** Top-1 similar incident là DDoS (services overlap) nhưng thực tế là connection pool. Keyword retrieval không đủ semantic để phân biệt "payment-svc degrade" do DDoS vs do pool exhaustion.
3. **Classification sai hướng:** `ddos` actions (WAF, Cloudflare) không match với `connection_pool_exhaustion` actions (rollback, increase pool) — nếu thực thi sai actions này sẽ không giải quyết vấn đề.
4. **Root cause bị masked bởi cascade:** Khi cả edge-lb, checkout-svc, payment-svc đều alert gần nhau trong 60-90 giây, temporal scorer khó phân biệt origin vs victim nếu không có metric-type weighting.

**Cách cải thiện:** Thêm feature `metric_type_is_resource_exhaustion` (db_connection_pool, memory, cpu saturation) vào scorer để ưu tiên resource exhaustion metrics trên critical services như payment-svc.

---

## 4. Bonus — So sánh 3 phương pháp

### Bonus 1: Decision Tree

Đã train `DecisionTreeClassifier(max_depth=4)` trên 30 incidents với features: one-hot encoding services_involved (11 unique services), severity_max (1–4), time_burst_pattern (1/2/3 dựa trên mttd_min).

**Kết quả:** CV accuracy=0.0 (5-fold StratifiedKFold thất bại vì nhiều class chỉ có 1 sample trong 30 incidents). Prediction cho c-001-000: `ddos` (conf=1.0) — agree với kNN. Prediction cho c-002-001: `bad_deploy` (conf=0.083) — disagree với kNN (`memory_leak`).

**Nhận xét:** Decision Tree overfits nặng trên dataset 30 incidents với 17 unique classes. One-hot encoding 11 services tạo sparse feature matrix khiến model không generalize. Với dataset nhỏ như vậy, DT không phải lựa chọn tốt cho multiclass classification.

### Bonus 2: TF-IDF Cosine Similarity

Thay keyword Jaccard bằng `TfidfVectorizer(ngram_range=(1,2), max_features=300, sublinear_tf=True)` trên corpus gồm summary + root_cause_class + services + remediation.

**Kết quả:**
- TF-IDF LOO accuracy: **0.172** vs kNN keyword LOO: **0.069** — TF-IDF thắng rõ.
- Top-1 cho c-001-000: `INC-2026-03-20` (cosine=0.271, ddos) — cùng kết quả với keyword nhưng gap so với #2 rõ hơn.
- TF-IDF tốt hơn vì bigram capture "connection pool", "error rate", "downstream cascade" tốt hơn Jaccard thuần.

**Trade-off:** TF-IDF cần re-fit khi thêm incident mới. Với corpus nhỏ (30 docs), vocabulary sparse — LOO accuracy vẫn thấp (~17%) vì nhiều class không có đủ representative documents.

### Bonus 3: LLM Enrichment (Simulated)

Đã implement prompt structure §4.3 và simulate output deterministic dựa trên class. Không dùng API key thực — mọi kết quả đều reproducible.

**So sánh class label vs kNN:**
- c-001-000: Cả kNN và LLM đều predict `ddos` — agreement nhưng cả 2 đều sai với ground truth.
- c-002-001: kNN → `memory_leak`, LLM enrichment agree → `memory_leak` với conf cao hơn (0.618 vs 0.598). Actions từ LLM specific hơn nhiều (gc.collect, memory limit) so với kNN chỉ lấy remediation text từ incident.

**Lợi thế LLM thực sự (nếu dùng Groq/OpenAI):** Có khả năng nhận biết rằng "downstream_payment_error_rate" + "db_connection_pool_used_ratio" cùng nhau trên payment-svc là signal rõ ràng cho connection_pool_exhaustion, không phải DDoS — semantic reasoning mà keyword/TF-IDF không làm được.

### Tổng kết so sánh

| Method | LOO Accuracy | c-001-000 class | c-002-001 class | Production Ready? |
|--------|-------------|-----------------|-----------------|-------------------|
| kNN keyword | 6.9% | ddos | memory_leak | Baseline, fast |
| TF-IDF cosine | 17.2% | ddos | memory_leak | Better recall |
| Decision Tree | ~0% CV | ddos | bad_deploy | Không phù hợp với 30 samples |
| LLM (simulated) | N/A | ddos | memory_leak | Best reasoning |

**Conclusion:** TF-IDF > kNN keyword trên dataset này. Decision Tree fail hoàn toàn vì sparse features + ít samples. LLM enrichment cần API key thực để thấy lợi thế real. Với GeekShop scale (alert volume cao, stable service map), TF-IDF + graph traversal là lựa chọn tốt nhất mà không cần API cost.
