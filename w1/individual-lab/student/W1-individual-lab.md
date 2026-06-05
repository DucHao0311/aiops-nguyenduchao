# AIOps W1 Lab Cá Nhân — "Streaming Anomaly Pipeline"

**Tuần:** AIOps W1 (Program W8)
**Hình thức:** Cá nhân | 3 giờ
**Nộp bài:** Cuối buổi lab
**Repo path:** `aiops-{tên-bạn}/w1/individual-lab/`

---

## Kịch bản

Bạn là on-call engineer cho ShopX. Hệ thống production đang chạy bình thường — nhưng sẽ có **một sự cố xảy ra bất kỳ lúc nào** trong buổi lab.

**Nhiệm vụ:** Xây pipeline chạy được: nhận data stream → phát hiện anomaly → ghi alert ra file.

---

## Cách hoạt động

```
┌──────────────┐     POST /ingest       ┌───────────────────┐
│  Generator   │ ──────────────────── → │  Pipeline CỦA BẠN │
│  (được phát) │                        │  (bạn tự xây)     │
└──────────────┘                        │                   │
                                        │  detect → alert   │
                                        │       ↓           │
                                        │  alerts.jsonl     │
                                        └───────────────────┘
```

1. Bạn chạy generator với ngày sinh của mình
2. Generator POST metrics + logs đến endpoint của bạn liên tục
3. Pipeline của bạn nhận data, phân tích, phát hiện anomaly
4. Khi phát hiện anomaly → ghi alert vào `alerts.jsonl`

---

## Khởi chạy Generator

```bash
uv run python stream_generator.py --birthday NGÀY-SINH-CỦA-BẠN --target http://localhost:PORT/ingest
```

Ví dụ:
```bash
uv run python stream_generator.py --birthday 2000-03-15 --target http://localhost:8000/ingest
```

**Quan trọng:** HTTP server của bạn phải chạy TRƯỚC khi bật generator. Generator POST liên tục — nếu endpoint chưa sẵn sàng, data sẽ bị mất (generator không queue lại).

---

## Cấu trúc Payload

Mỗi POST gửi đến endpoint của bạn:

```json
{
  "timestamp": "2026-06-05T09:15:03.412+00:00",
  "metrics": {
    "memory_usage_bytes": 823000000,
    "memory_limit_bytes": 2000000000,
    "cpu_usage_percent": 32.1,
    "http_requests_per_sec": 145.3,
    "http_p99_latency_ms": 52.7,
    "http_5xx_rate": 0.31,
    "jvm_gc_pause_ms_avg": 14.2,
    "queue_depth": 6,
    "upstream_timeout_rate": 0.08
  },
  "logs": [
    {
      "timestamp": "2026-06-05T09:15:03.412+00:00",
      "level": "INFO",
      "service": "cart-service",
      "pod": "cart-service-7821",
      "message": "Request processed successfully"
    }
  ]
}
```

### Bảng metrics

| Field | Đơn vị | Khoảng bình thường | Mô tả |
|-------|--------|--------------------:|--------|
| `memory_usage_bytes` | bytes | ~800M ± 20M | RAM container |
| `memory_limit_bytes` | bytes | 2,000,000,000 | Giới hạn K8s (hằng số) |
| `cpu_usage_percent` | % | 20–45 | CPU |
| `http_requests_per_sec` | req/s | 80–160 (có chu kỳ ngày/đêm) | Lưu lượng request |
| `http_p99_latency_ms` | ms | 35–65 | Độ trễ P99 |
| `http_5xx_rate` | % | 0–0.8 | Tỷ lệ lỗi |
| `jvm_gc_pause_ms_avg` | ms | 8–18 | Thời gian GC |
| `queue_depth` | count | 2–10 | Hàng đợi request |
| `upstream_timeout_rate` | % | 0–0.4 | Tỷ lệ timeout upstream |

### Logs

Mảng 0–3 log entries mỗi tick. Levels: `INFO`, `WARN`, `ERROR`, `FATAL`.

---

## Định dạng Alert

Khi pipeline phát hiện anomaly, ghi **một dòng JSON** vào `alerts.jsonl`:

```json
{"timestamp": "2026-06-05T10:23:45.000+00:00", "type": "memory_leak", "severity": "critical", "message": "Memory usage growing abnormally, utilization at 85%"}
```

| Field | Bắt buộc | Mô tả |
|-------|:--------:|--------|
| `timestamp` | ✓ | ISO 8601 — thời điểm bạn fire alert |
| `type` | ✓ | Loại fault bạn nghĩ đang xảy ra: `memory_leak`, `traffic_spike`, hoặc `dependency_timeout` |
| `severity` | ✓ | `warning` hoặc `critical` |
| `message` | ✓ | Mô tả ngắn evidence bạn thấy |

---

## Yêu cầu

### Bắt buộc (PASS/FAIL)

1. **HTTP endpoint** nhận POST request từ generator (bất kỳ framework/ngôn ngữ nào)
2. **Logic phát hiện anomaly** hoạt động trên streaming data
3. **Ghi alert** → `alerts.jsonl` khi phát hiện anomaly
4. **DESIGN.md** — giải thích approach detection của bạn và lý do chọn nó

### Điểm bonus

| Bonus | Điều kiện |
|-------|-----------|
| +1 | Xác định đúng loại fault (field `type` trong alert khớp fault thật) |
| +1 | Không có false alert trước khi fault xảy ra |
| +1 | Phát hiện nhanh (TTD thấp) |

---

## Cấu trúc thư mục

```
aiops-{tên-bạn}/
  w1/
    individual-lab/
      pipeline.py          # (hoặc main.go, index.js — file chính)
      alerts.jsonl         # output — pipeline tạo ra
      DESIGN.md            # giải thích approach detection
      requirements.txt     # hoặc pyproject.toml — dependencies
```

---

## Setup (ví dụ Python)

```bash
uv pip install fastapi uvicorn
```

Skeleton khởi đầu (bạn cần thêm logic detection):

```python
from fastapi import FastAPI, Request
import json, uvicorn

app = FastAPI()
ALERTS_FILE = "alerts.jsonl"

@app.post("/ingest")
async def ingest(request: Request):
    payload = await request.json()
    metrics = payload["metrics"]
    logs = payload["logs"]
    timestamp = payload["timestamp"]

    # TODO: logic detection của bạn ở đây
    # if anomaly_detected:
    #     alert = {"timestamp": timestamp, "type": "...", "severity": "critical", "message": "..."}
    #     with open(ALERTS_FILE, "a") as f:
    #         f.write(json.dumps(alert) + "\n")

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## DESIGN.md Template

```markdown
# Detection Approach — DESIGN.md

## Approach tôi dùng
<!-- Tên kỹ thuật -->

## Tại sao chọn approach này
<!-- Giải thích tại sao phù hợp streaming -->

## Cách hoạt động
<!-- 1 đoạn giải thích thuật toán -->

## Parameters tôi chọn
<!-- Window size, threshold, v.v. — và lý do -->

## Cải thiện nếu có thêm thời gian
<!-- Nhận xét thật -->
```

---

## Chấm điểm

```
PASS = pipeline chạy được + alert fire khi có anomaly
FAIL = không có alert / pipeline không chạy
```

Trainer chạy grader sau khi lab kết thúc — bạn không cần quan tâm script chấm.
