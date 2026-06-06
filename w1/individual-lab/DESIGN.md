# Detection Approach — DESIGN.md

## Approach tôi dùng

**Sliding Window + Threshold / Z-Score Detection** với logic riêng cho từng loại fault.

---

## Tại sao chọn approach này

Streaming data đến theo từng tick đều đặn (30s production time mỗi tick). Sliding window phù hợp vì:

- **Không cần train offline**: baseline được tính trực tiếp từ các ticks gần nhất trong window, thích nghi với diurnal pattern (traffic cao ban ngày, thấp ban đêm).
- **Latency thấp**: mỗi tick chỉ cần O(window_size) tính toán, không block.
- **Interpretable**: threshold và z-score dễ tune và debug so với black-box model.
- **Phù hợp 3 loại fault khác nhau**: mỗi fault có signal đặc trưng nên dùng rule riêng chính xác hơn một model chung.

---

## Cách hoạt động

Pipeline giữ một sliding window 20 ticks (~10 phút production time) của các metrics. Mỗi tick mới đến, 3 detector chạy song song:

1. **memory_leak detector**: tính slope trung bình (delta memory mỗi tick) và memory utilization. Nếu memory tăng đều đặn qua nhiều ticks AND utilization > 75%, fire alert. GC pause cao là tín hiệu phụ xác nhận.

2. **traffic_spike detector**: tính z-score của RPS hiện tại so với mean/std của window. Z-score > 2.5σ là bất thường. Kết hợp queue_depth và P99 latency để xác nhận (tránh false positive khi traffic tăng do diurnal pattern).

3. **dependency_timeout detector**: so sánh `upstream_timeout_rate` và `http_5xx_rate` với threshold tuyệt đối (phần trăm). Scan logs để tìm "circuit breaker OPEN" làm tín hiệu early warning.

Cooldown 10 ticks giữa 2 alert cùng loại để tránh alert spam.

---

## Parameters tôi chọn

| Parameter | Giá trị | Lý do |
|-----------|---------|-------|
| `WINDOW_SIZE` | 20 ticks | ~10 phút production time — đủ dài để có baseline ổn định, đủ ngắn để phát hiện nhanh |
| `COOLDOWN_TICKS` | 10 | Tránh spam alert cùng loại, tối thiểu 5 phút production time giữa 2 alert |
| `MEMORY_UTIL_WARNING` | 75% | Ngưỡng bắt đầu lo ngại memory; normal range ~40-50% |
| `MEMORY_UTIL_CRITICAL` | 85% | Gần limit 2GB, cần action ngay |
| `MEMORY_SLOPE_WARN` | 30MB/tick | Growth rate bất thường so với noise baseline (~20M variance) |
| `RPS_ZSCORE_WARN` | 2.5σ | 2.5σ tương ứng ~1.2% false positive rate trên normal distribution |
| `RPS_ZSCORE_CRIT` | 4.0σ | Spike rõ ràng (traffic_spike inject lên 8x) |
| `UPSTREAM_TIMEOUT_WARN` | 5% | Normal range 0-0.4%, 5% là tín hiệu rõ ràng |
| `HTTP_5XX_CRIT` | 15% | Normal range 0-0.8%, 15% là ảnh hưởng nghiêm trọng đến user |

---

## Cải thiện nếu có thêm thời gian

- **EWMA (Exponentially Weighted Moving Average)** thay cho simple window mean — phản ứng nhanh hơn với thay đổi đột ngột mà vẫn smooth noise.
- **Seasonal baseline**: tách diurnal pattern ra khỏi anomaly signal (RPS tăng vào giờ cao điểm không phải spike).
- **Multi-signal correlation**: chỉ fire alert khi ≥2 signals cùng bất thường (giảm false positive).
- **Prometheus metrics endpoint** để monitor health của chính pipeline.
- **Severity escalation**: tự động escalate từ warning → critical nếu anomaly kéo dài > N ticks.
