# W2 D1 — Alert Correlation Pipeline: SUBMIT
Trả lời cho các câu hỏi yêu cầu cho bài tập


## 7.3 Design Decisions

### Tại sao chọn `gap_sec = 300`?

Chọn `gap_sec = 300` (5 phút) vì toàn bộ incident trong `alerts_sample.jsonl` kéo dài từ `09:42:01Z` đến `09:48:30Z` — tức ~6,5 phút. Một gap 5 phút đủ rộng để gom tất cả các alert liên quan trong cùng một burst mà không cần tạo nhiều window rời rạc. Nếu chọn quá nhỏ (ví dụ 30 giây), các alert cùng fingerprint như `payment-svc|latency_p99_ms|crit` bị fired lúc `09:42:22`, `09:43:18`, `09:46:01` sẽ bị tách thành 3 window riêng biệt, dẫn đến merge phức tạp hơn hoặc không merge được. Nếu chọn quá lớn (ví dụ 3600 giây), những alert noise hoàn toàn độc lập xảy ra vào buổi trưa cùng ngày cũng có thể bị kéo vào cùng cluster — false positive tăng. `gap_sec = 300` là điểm cân bằng giữa sensitivity và specificity cho incident duration điển hình trong môi trường e-commerce production.

### Tại sao chọn `max_hop = 1`?

Chọn `max_hop = 1` vì trong topology của GeekShop, root cause (payment-svc pool exhaustion) và các symptom downstream (checkout-svc, edge-lb) đều là **direct neighbors** hoặc cách nhau đúng 1 hop. `max_hop = 1` đủ để merge các cặp: `payment-svc ↔ checkout-svc` (direct edge), `checkout-svc ↔ edge-lb` (direct edge), `checkout-svc ↔ notification-svc` (qua kafka). Nếu tăng lên `max_hop = 2`, toàn bộ graph sẽ được kết nối vì `catalog-db` là single point connecting 4 services — dẫn đến over-merging và gom cả `recommender-svc` vào cluster chính dù alert của nó là batch retrain hoàn toàn độc lập. `max_hop = 1` giúp giữ **precision** cao, không merge những service chỉ share một backing store không liên quan đến incident.

**Design trade-off:** `max_hop = 1` có thể miss correlation nếu propagation xảy ra qua 2+ hops (ví dụ: `search-svc → catalog-db → inventory-svc`). Trong trường hợp này, `a-0016` (search-svc slow query) vẫn được kéo vào cluster chính vì `search-svc` có edge đến `catalog-db`, và `catalog-db` connect đến `checkout-svc` path. Đây là acceptable false positive so với rủi ro over-merging của `max_hop = 2`.

### Alert ID nào bị "miss" (không match cluster nào)?

**Không có alert nào hoàn toàn bị miss** — tất cả 20 alerts đều được assign vào một cluster. Tuy nhiên, `a-0013` (`recommender-svc|cpu_utilization|warn`) được gom vào **cluster riêng biệt** (`c-002-001`) thay vì cluster chính. Lý do: `recommender-svc` không có direct edge đến `payment-svc`, `checkout-svc`, hay `edge-lb` trong topology (chỉ connect đến `catalog-svc` và `catalog-db`). Trong khi đó, `catalog-svc` không có alert nào trong incident này, nên không có bridge để merge `recommender-svc` vào cluster chính. Đây là kết quả **đúng** — note trong data xác nhận đây là "unrelated — concurrent batch retrain".

Nếu muốn có 1 alert "truly missed" (không vào cluster nào), đó là scenario khi một alert có service không tồn tại trong topology graph — ví dụ: một service external hoặc misconfigured alert không có node tương ứng. Trong dataset hiện tại không có trường hợp này.

### Nếu có 10,000 alerts thay vì 20, code sẽ chậm ở đâu?

Code sẽ chậm nhất ở **Phase 2 — topology merge loop**. Đây là O(n²) per iteration (nested loop qua tất cả cặp windows), và mỗi iteration có thể trigger thêm vòng lặp mới nếu còn merge được. Với 10.000 alerts, nếu Phase 1 tạo ra ~5.000 windows, Phase 2 sẽ phải check ~12,5 triệu cặp per pass. Ngoài ra, hàm `get_neighbors_within_hops()` được gọi lặp lại nhiều lần với cùng input — cần cache (memoize) kết quả BFS. Giải pháp scale: (1) dùng Union-Find (Disjoint Set) thay nested loop, (2) pre-compute adjacency matrix thay BFS mỗi lần, (3) index alerts bằng time bucket để chỉ check pairs gần nhau về thời gian.

---

## 8. EOD Checkpoint

### Câu 1: Vì sao fingerprint không include timestamp hay value?

Fingerprint = `service|metric|severity` — không có timestamp và value — vì mục đích của fingerprint là **nhận dạng loại triệu chứng**, không phải một event cụ thể. Timestamp thay đổi mỗi giây nên mỗi alert sẽ có fingerprint duy nhất, phá vỡ hoàn toàn logic deduplication. Ví dụ: nếu include timestamp, `payment-svc|latency_p99_ms|crit` bị fired lúc `09:42:22`, `09:43:18`, `09:46:01` sẽ tạo ra 3 fingerprint khác nhau → hệ thống tạo 3 cluster riêng biệt → on-call nhận 3 page thay vì 1. Value cũng tương tự: `latency = 1840ms` vs `latency = 1750ms` là cùng một vấn đề, không phải hai vấn đề khác nhau. Include value → noise tăng, deduplication rate giảm gần về 0.

### Câu 2: Sự khác biệt giữa "duplicate" và "correlated" alert?

**Duplicate**: Cùng service, cùng metric, cùng severity — alert được fired lại do alerting rule evaluate nhiều lần trong khi condition vẫn còn đó. Ví dụ từ dataset: `a-0003`, `a-0008`, `a-0015` — cả ba đều là `payment-svc|latency_p99_ms|crit` với cùng value `1840ms`. Đây là cùng một condition firing 3 lần, không có thông tin mới.

**Correlated**: Các alert **khác nhau** (khác service hoặc metric) nhưng có chung root cause. Ví dụ từ dataset: `a-0002` (`payment-svc|db_connection_pool_used_ratio|crit`) và `a-0006` (`checkout-svc|downstream_payment_error_rate|crit`) — hai fingerprint hoàn toàn khác nhau, nhưng cùng xuất phát từ việc payment-svc pool exhausted. Nếu chỉ dedup mà không correlate, on-call vẫn nhận 2 page từ 2 team khác nhau (payments-oncall + checkout-oncall) cho cùng một incident.

### Câu 3: `gap_sec = 30` vs `gap_sec = 600`

- **`gap_sec = 30`**: Chỉ gom các alert cách nhau ≤30 giây → `payment-svc|latency_p99_ms|crit` ở `09:42:22` và `09:43:18` (cách 56 giây) sẽ tạo 2 window riêng → nhiều cluster nhỏ hơn, reduction ratio thấp hơn, topology merge phức tạp hơn, tăng nguy cơ tách incident thành nhiều ticket.
- **`gap_sec = 600`**: Gom tất cả alerts trong 10 phút → toàn bộ 20 alerts (kể cả `recommender-svc` batch retrain lúc `09:45:10` và `search-svc` slow query `09:46:50`) đều nằm trong window `09:42–09:48` → merge vào 1 cluster duy nhất → over-correlation, false positive cao, root cause bị pha loãng bởi noise.

### Câu 4: Recommender-svc có bị gom vào cluster chính không? Tại sao?

**Không** — `recommender-svc` được gom vào cluster riêng (`c-002-001`) với `max_hop = 1`.

Lý do topology: Nhìn vào `services.json`, `recommender-svc` chỉ có edges đến `catalog-svc` (qua HTTP) và `catalog-db` (qua postgres). Để reach được `payment-svc` hay `checkout-svc` từ `recommender-svc`, cần ít nhất 2 hops: `recommender-svc → catalog-svc → cart-svc → checkout-svc`. Với `max_hop = 1`, BFS từ `recommender-svc` chỉ reach được `{catalog-svc, catalog-db}` — không overlap với service set của cluster chính `{payment-svc, checkout-svc, edge-lb, ...}`.

Đây là kết quả **đúng về mặt logic**: alert của `recommender-svc` là CPU spike do batch retrain job (có note "unrelated — concurrent batch retrain" trong data). Nó xảy ra cùng thời điểm nhưng hoàn toàn độc lập với payment-svc pool exhaustion. Topology-aware correlation giúp phân biệt được **temporal coincidence** (cùng lúc) khỏi **causal relationship** (cùng nguyên nhân). Nếu tăng `max_hop = 2`, correlator SẼ gom `recommender-svc` vào cluster chính (vì share `catalog-svc` làm bridge), đây là ví dụ điển hình của over-merging do path quá dài không phản ánh causal dependency thực sự.

### Câu 5: Limitation lớn nhất của topology grouping và cách khắc phục

**Limitation**: Topology graph là **static** — được định nghĩa một lần trong `services.json` và không thay đổi theo runtime. Trong thực tế, service dependencies thay đổi liên tục: feature flags enable/disable integration, A/B test tạo ra call path mới, canary deployment routing thay đổi traffic flow. Một alert từ `service-A` có thể liên quan đến `service-B` chỉ khi deployment version X đang active, nhưng topology static không biết điều này. Kết quả: correlator bỏ sót correlation thực (false negative) hoặc gom nhầm alert theo path đã deprecated (false positive).

**Đề xuất khắc phục**: Thay static topology bằng **dynamic service mesh-derived topology** — tự động crawl topology từ distributed tracing system (Jaeger/Zipkin) hoặc service mesh (Istio/Envoy) dựa trên **actual traffic trong sliding window 15 phút gần nhất**. Mỗi alert trigger một micro-lookup: "trong 15 phút trước alert này, service A có actual HTTP call đến service B không?" — nếu có và error rate tăng đồng thời, đây là correlated. Approach này handle được dynamic dependencies, blue/green deployments, và circuit breaker states mà static graph không thể capture.
