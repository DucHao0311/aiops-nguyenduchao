# W3-D1 Submission — Nguyen Duc Hao

## 3 thứ tôi học được

1. **Burn rate normalize SLO boundary, khiến cùng một ngưỡng dùng được cho mọi service.** Trước đây tôi nghĩ chỉ cần alert khi error rate > X%. Nhưng "5% error rate" có ý nghĩa hoàn toàn khác với SLO 99% (burn rate = 5) so với SLO 99.9% (burn rate = 50). MWMBR giải quyết bài toán này bằng cách chia error rate cho `(1 - SLO_target)` — một ngưỡng 14.4 mang ý nghĩa nhất quán là "đốt 2% budget trong 1 giờ" bất kể SLO của service là bao nhiêu.

2. **AND condition giữa long window và short window là key quyết định chất lượng alerting.** Long window (1h/6h) đảm bảo signal đủ lớn để đáng action — loại bỏ spike nhất thời 1–2 phút. Short window (5m/30m) đảm bảo incident vẫn đang diễn ra ngay lúc alert fire — và quan trọng hơn, cho phép alert recover nhanh (~5 phút sau khi sự cố hết). Nếu bỏ short window, alert sẽ "dính" 30–60 phút sau khi service đã recover, gây alert fatigue nghiêm trọng.

3. **4xx không phải system error — đây là ranh giới quan trọng nhất khi thiết kế SLI.** Baseline data cho thấy 4xx rate = 2.01% trên tất cả 5 endpoints (`/api/cart`, `/api/orders`, v.v.) hoàn toàn đồng đều — đây là background noise từ bot, session expiry, bad client request. Nếu đếm 4xx vào fail, SLI sẽ là 2.36% error, khiến SLO 99% không bao giờ đạt được. Chỉ có 429 (rate-limited by system) mới là system-owned failure.

---

## 1 thứ vẫn chưa rõ

**Cách set SLO target cho service hoàn toàn mới không có baseline dữ liệu lịch sử.** Lab này có 3-day synthetic data để compute baseline, nhưng trong thực tế khi deploy service mới, không có lịch sử. Google SRE Workbook gợi ý bắt đầu với "aspirational SLO" (ví dụ 99%) rồi tighten dần sau 1–2 tháng quan sát. Tuy nhiên, câu hỏi là: làm thế nào để set SLO ban đầu đủ chặt để có ý nghĩa, nhưng không quá chặt đến mức miss ngay tháng đầu? Liệu có framework nào tốt hơn "guess and iterate" cho greenfield service không?

---

## 1 trade-off trong SLO decision của tôi mà tôi không chắc

**SLO api = 99.9% với mttd_delta = 60 s so với static baseline.** Acceptance criteria yêu cầu `mttd_delta < 60 s`, và kết quả validation cho đúng = 60 s (biên). Tôi có thể tune threshold xuống 12 để detect nhanh hơn, giảm mttd_delta xuống 0 s, nhưng điều đó làm tăng nguy cơ FP khi có traffic spike nhất thời. Tôi chọn giữ Google default 14.4 vì FP = 0 quan trọng hơn — alert fatigue là kẻ thù số 1 của on-call engineer, và 60 s MTTD delta hoàn toàn acceptable cho incident detection (so với 8 phút duration của incident #1). Tuy nhiên, tôi không 100% chắc đây là quyết định đúng cho production environment có SLA nghiêm ngặt hơn.

---

## Validation report

- **noise_reduction_pct:** 86.4%
- **mttd_delta_s:** 60 s
- **false_negative:** 0
- **verdict:** pass
