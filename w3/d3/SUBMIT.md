# W3-D3 Submission — Nguyen Duc Hao

---

## Outage Chosen

- **ID:** 3
- **Name:** Cloudflare WAF regex (2019-07-02)
- **Why this one:** Pattern catastrophic backtracking là một trong những failure mode ít được nhắc đến nhưng có tác động toàn cầu tức thì — một file rule duy nhất có thể down toàn bộ infrastructure trong vòng giây mà không có bất kỳ service crash nào. Tôi muốn tìm hiểu liệu AIOps pipeline có thể phân biệt "service đang bận" vs "service đang chết vì CPU" không, và quan trọng hơn — liệu pipeline có thể làm gì với failure mode này ngoài việc detect sau khi đã too late.
- **Failure mode:** `catastrophic_backtracking` — Regex / parser exponential time on adversarial input (§4 catalog pattern #3)

---

## 3 Thứ Tôi Học Từ Outage Này

1. **Có những failure class mà runtime detection là điều kiện cần nhưng không đủ — phải shift left hoàn toàn.**  
   MTTD = 4 giây nghe rất tốt trong mọi context khác. Nhưng trong Cloudflare 2019, 4 giây trên 7 triệu request/giây = 28 triệu requests failed trước khi alert đầu tiên fire. Pipeline của tôi phát hiện đúng và nhanh, nhưng "nhanh" ở đây vẫn là "sau khi hệ thống đã sụp". Điều này dạy tôi phân loại failure mode theo khả năng ngăn chặn: **prevent (pre-deploy gate)** vs **detect+remediate (runtime pipeline)** — và phải chọn đúng weapon cho đúng loại threat.

2. **Async event loop + blocking synchronous work = single point of failure cho toàn bộ service.**  
   Khi regex engine block CPU trong uvicorn event loop, không chỉ request có adversarial input bị stuck — *tất cả* requests kế tiếp cũng bị queue, kể cả `/healthz`. Service trông như dead với load balancer và container orchestrator mặc dù process vẫn đang chạy (chỉ là đang bận vô tận với backtracking). Đây là kiến trúc anti-pattern: bất kỳ CPU-intensive synchronous work nào trong async middleware cần được offload sang thread pool. Lesson: một process "alive" không có nghĩa là "available."

3. **Static regex complexity analysis là một trong số ít security checks có thể có 100% precision và 0 production cost.**  
   Phần lớn security controls có trade-off (false positive rate, performance overhead, UX friction). ReDoS static analysis (`recheck` library) là ngoại lệ: nếu regex được xác nhận là linear complexity, không thể backtrack catastrophically — đây là mathematical guarantee, không phải probabilistic. Chi phí: < 5 giây CI step. Kết quả: loại bỏ hoàn toàn một failure class. Đây là một trong số ít engineering decisions có asymmetric payoff: cost rất thấp, value tuyệt đối. Tôi sẽ áp dụng nguyên tắc này cho mọi validate-able input (PromQL complexity, Drain template depth, feature expression complexity) trong AIOps pipeline.

---

## 1 Thứ Pipeline Của Tôi Sẽ Vẫn Miss Nếu Outage Này Xảy Ra Real

- **Pattern:** Catastrophic backtracking + global atomic deploy
- **Why miss:** Pipeline detect được CPU saturation và HighLatency đúng (MTTD = 4s trong lab). Nhưng trong real Cloudflare incident, failure không xảy ra ở một container — nó xảy ra *đồng thời trên toàn bộ edge infrastructure toàn cầu* vì WAF rule được deploy atomic 100% thay vì canary. Pipeline của tôi thiết kế để detect fault ở một service trong một stack. Khi *tất cả* services (hoặc tất cả PoPs) fail cùng lúc do một shared config change, topology-aware RCA breakdown: không có "upstream" healthy service để xác định depth, không có "first-drift" service vì tất cả drift cùng lúc, và correlator sẽ tạo ra một cluster khổng lồ với confidence thấp vì mọi service đều showing symptoms. RCA output sẽ là "tất cả services bị ảnh hưởng" — đúng nhưng vô dụng để mitigation.
- **Mitigation idea:** Thêm một **deploy correlation signal** vào pipeline: khi nhiều services fire alerts trong cùng một khoảng 60 giây, và có một deployment event trong change log trong cùng window đó, RCA tự động include "recent deploy" vào top-3 hypothesis với high confidence. Đây là "change causation heuristic" — không phải topology analysis mà là temporal correlation với change events. Công cụ cụ thể: ingest deployment events từ CI/CD system (GitHub Actions webhook, ArgoCD events) vào pipeline event stream, và tạo `DeployCorrelatedIncident` RCA category khi P(incident | deploy_within_60s) > 0.8.

---

## 1 Quyết Định Trong ADR Mà Tôi Không Hoàn Toàn Chắc

**Quyết định trong ADR-008:** Reject toàn bộ rules có `worst_case_class IN {polynomial, exponential}` — blocking hard reject, không phải warning.

**Tôi không chắc vì:** Static complexity analysis có thể false positive với một số regex hợp lệ về mặt security nhưng có worst-case polynomial trên inputs không bao giờ xuất hiện trong production (ví dụ: regex phức tạp cho một attack pattern cụ thể, adversarial input cần > 1000 characters để trigger backtracking, nhưng thực tế HTTP header limit là 8KB). Nếu hard-reject quá nhiều rules hợp lệ, security team sẽ workaround bằng `SECURITY_OVERRIDE` cho mọi rule → cơ chế reject trở nên vô nghĩa.

Tôi cân nhắc alternative: thay hard-reject bằng "worst-case benchmark at n=50" (test thực tế thay vì worst-case theoretical) — nếu p99 evaluation time < 10ms tại input length 50, pass; nếu không, reject. Approach này pragmatic hơn nhưng tôi chưa chắc nó đủ safe: adversarial input đặc biệt có thể trigger backtracking ở input length 50 mà synthetic test corpus không cover.

---

## Cost Model Verdict Cho Stack Của Tôi

- **Scenario:** Payment gateway fintech — 80 services, 4 incidents/month × 1.5h avg duration, $30,000/hour downtime cost
- **ROI:** 1.80x
- **Payback:** 0.56 tháng (~17 ngày)
- **Verdict:** WORTH_IT

**Lý do chọn $30k/hr downtime cost:** Stack là payment gateway xử lý $2M/giờ card transactions. Trong downtime, ước tính 15% transactions irrecoverable (user rời đi, không retry sau) = $300k/hr revenue risk. Cộng $5k/hr SLA penalty cho enterprise banking clients + $500/event PCI DSS reporting overhead. Conservative estimate: $30k/hr (thực tế peak exposure có thể 3–10× cao hơn). Ngay cả với $30k/hr, ROI = 1.8x → worth_it. Nếu dùng realistic peak ($50k/hr): ROI = 3.0x, payback = 10 ngày.

---

## Acceptance Checklist (Self-Check)

| Item | Status |
|------|--------|
| Reproduction chạy được, inject.sh trigger failure mode quan sát được | ✓ (cloudflare_regex_2019: docker compose + inject.sh flip EVIL_REGEX_ACTIVE=1) |
| timeline.json có ≥ 8 events với UTC timestamp | ✓ (15 events, UTC ISO8601 format) |
| postmortem.md: đủ field theo template §2, 0 blame wording, timeline ≥ 8 events, ≥ 2 gap detection | ✓ (all fields filled, blameless language throughout, 8 timeline events in lab section, 2 gaps documented) |
| ADR.md: ≥ 2 alternatives với pros/cons, ≥ 2 consequences, reference 1 gap từ §9.4 | ✓ (3 alternatives with detailed pros/cons, 2+ consequences, Gap 2 from rca_observed.json referenced explicitly) |
| cost_model.py: parseable, is_worth_it() return đúng schema, 3 worked examples | ✓ (runs clean, all 3 scenarios verified, schema matches §8.3 spec) |
| SPEC.md: 7 sections đầy đủ | ✓ (sections 1–7 all present with content) |
| SUBMIT.md: 5 sections đầy đủ | ✓ (this file) |
