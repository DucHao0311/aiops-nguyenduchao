# SUBMIT — Day 2: Log Parsing & Anomaly Detection (HDFS)

---

## Phase 1: Parse Log với Drain3

### 1.1 Load dữ liệu

- **Dataset sử dụng:** HDFS_2k.log (Loghub — HDFS)
<img width="1548" height="883" alt="image" src="https://github.com/user-attachments/assets/c11e10b2-df8c-4416-b0ce-13e301545f8e" />

- **Tổng số dòng log đọc được:** 2000
<img width="1087" height="326" alt="image" src="https://github.com/user-attachments/assets/7be83dee-987c-41f3-832f-23c84d02d9f3" />


### 1.2 Parse toàn bộ log với Drain3

Cấu hình tối ưu được chọn:
- `drain_sim_th = 0.5`
- `drain_depth = 5` (tăng từ 4 → phân biệt component tốt hơn)
- Fix timestamp: prefix `"20"` vào YYmmdd → parse đúng năm 2008

**Kết quả:**
- Số lượng template độc nhất tìm thấy: **21 templates**
<img width="1400" height="296" alt="image" src="https://github.com/user-attachments/assets/2c08caa1-2c7e-47e5-92d9-ed1e23386bf9" />


### 1.3 Top-10 Templates (export ra `results/top_templates.csv`)

| template_id | template | count |
|---|---|---|
| T-2 | `<*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlock: blockMap updated: <*> is added to <*> size <*>` | 314 |
| T-1 | `<*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*> for block <*> terminating` | 311 |
| T-4 | `<*> <*> <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> src: <*> dest: <*>` | 292 |
| T-3 | `<*> <*> <*> INFO dfs.DataNode$PacketResponder: Received block <*> of size <*> from <*>` | 292 |
| T-7 | `<*> <*> <*> INFO dfs.FSDataset: Deleting block <*> file <*>` | 263 |
| ... | *(xem file results/top_templates.csv để xem đầy đủ)* | ... |
<img width="1351" height="406" alt="image" src="https://github.com/user-attachments/assets/bacc21e8-64d4-4e82-82ed-342860ffa52b" />


### 1.4 Tuning `drain_sim_th`

| drain_sim_th | Số template độc nhất |
|---|---|
| 0.3 | 17 |
| **0.5** | **21** |
| 0.7 | 820 |
<img width="741" height="277" alt="image" src="https://github.com/user-attachments/assets/cbcfaa0d-9300-4528-bc32-fa21a30c0dd4" />


**Nhận xét:**
- `sim_th = 0.3`: Ngưỡng thấp → Drain3 gộp nhiều log thành cùng template → chỉ ra 17 template, quá tổng quát, mất chi tiết.
- `sim_th = 0.5`: Cân bằng giữa chi tiết và tổng quát → **21 template**, phù hợp với đặc điểm lặp lại của HDFS log.
- `sim_th = 0.7`: Ngưỡng cao → Drain3 tách quá tinh, tạo ra 820 template, hầu hết là do biến số số/địa chỉ bị coi là khác nhau → không thực tế.

**Chọn `drain_sim_th = 0.5`** là tối ưu nhất cho HDFS dataset.

---

## Phase 2: Anomaly Detection trên Log

### 2.1 Tạo Template Count Time Series

- Time window: **5 phút**
- Bảng time series có shape: `(N_windows, 21_templates)`

### 2.2 Anomaly Detection — 3σ Spike + WARN Signal (tối ưu)
<img width="859" height="409" alt="image" src="https://github.com/user-attachments/assets/0b532c68-261b-46ae-9c4b-3a1ba4a9bfa6" />

Strategy 2 signal kết hợp OR:
- **Signal 1 — 3σ count spike:** window có tổng log > mean + 3×std
- **Signal 2 — WARN flag:** window có ít nhất 1 WARN-level log (exception thực sự)

| Thông số | Giá trị |
|---|---|
| Active windows | 305 |
| Mean log/window | 6.56 |
| Std | 8.72 |
| 3σ threshold | 32.7 |
| Count spike windows | 9 |
| WARN-based windows | 55 |
| **Tổng anomaly windows** | **64** |
| **Precision** | **1.000** |
| **Recall** | **0.955** |
| **F1** | **0.977** |

**Top anomaly windows:**

| Loại | Thời gian | Total | WARN | Top template |
|---|---|---|---|---|
| SPIKE | 2008-11-10 10:30 | 33 | 0 | T-2 (addStoredBlock) |
| SPIKE | 2008-11-10 10:35 | 16 | 0 | T-7 (Deleting block) |
| WARN | 2008-11-09 21:40 | 2 | 2 | T-9 (Got exception while serving block) |

### 2.3 Biểu đồ Template Count Time Series + Anomaly (cải tiến)

<img width="1447" height="871" alt="image" src="https://github.com/user-attachments/assets/bd7fd597-3cf2-4434-b0ec-7aa7a6742289" />

> - **Subplot trên:** đường log count/5min (xanh), đường 3σ (cam nét đứt), đường mean (xanh lá),
>   tam giác đỏ = count spike, chấm cam = WARN anomaly
> - **Subplot dưới:** bar chart WARN log count/5min (đỏ) — thấy rõ các cụm WARN rải rác
> - Trục X: thời gian đúng 2008-11-09 đến 2008-11-11

---

## Phase 3: Embedding + Cross-Signal

### 3.1 TF-IDF trên Templates → Similarity Matrix → Clusters

- Vector hóa 21 template bằng TF-IDF (token = chữ cái, bỏ qua `<*>`)
- Tính cosine similarity matrix (21×21)
- Phân cụm bằng Agglomerative Clustering với `n_clusters = 5`

**Kết quả phân cụm tiêu biểu:**

<img width="1007" height="858" alt="image" src="https://github.com/user-attachments/assets/dffcd249-1c0d-4f12-9afc-09ecca9f1050" />

> - Ma trận 21×21, màu càng đậm (vàng/đỏ) = similarity càng cao
> - Các nhóm có màu đậm theo đường chéo = cluster template tương đồng

Ví dụ nhóm cluster điển hình:
- **Cluster "Block transfer"**: T-2, T-3, T-4 (addStoredBlock, Received block, Receiving block) — các log liên quan vận chuyển block
- **Cluster "Packet Responder"**: T-1, T-5 — log PacketResponder terminating/serving
- **Cluster "Deletion/cleanup"**: T-7, T-8 — log xóa block, file cleanup
<img width="991" height="735" alt="image" src="https://github.com/user-attachments/assets/f20ed735-0474-4292-a18f-ea753160f9de" />


### 3.2 Inject Dòng Log "Lạ" → New Template Detection

**Dòng log inject:**
```
2008110 120000 WARN custom.FakeService: DISK_ERROR sector 99 corrupt, remapping failed at block 0xDEADBEEF
```

<img width="1311" height="247" alt="image" src="https://github.com/user-attachments/assets/187181ec-e3ce-4256-ae71-74451ecc172e" />

**Kết quả:**
- Số template TRƯỚC khi inject: **21**
- Số template SAU khi inject: **22**
- Drain3 tạo template MỚI: **True**
- Template được gán: `<*> <*> WARN custom.FakeService: DISK_ERROR sector <*> corrupt, remapping failed at block <*>`

**Kết luận:** Drain3 phát hiện cú pháp hoàn toàn lạ so với tập log hiện có và tạo một cluster mới — đây là dấu hiệu anomaly (new template alert).

---

## Phase 4: Challenge — Mini Log Analyzer

### 4.1 Script `log_analyzer.py`

Script nhận 1 argument là đường dẫn log file, output ra stdout:

```bash
python log_analyzer.py <logfile>
```

<img width="1843" height="831" alt="image" src="https://github.com/user-attachments/assets/f6a89bdb-6861-4bb3-93e4-53dfeb90a6fd" />


**Các thông tin output:**
1. Tổng số dòng, số template unique
2. Top-5 template (count + % tổng)
3. Template tăng đột biến trong 1 giờ gần nhất (so với tốc độ trung bình trước đó, ratio > 3x)
4. New templates chưa xuất hiện trước 1 giờ gần nhất

### 4.2 Test Script trên HDFS Dataset
<img width="1254" height="466" alt="image" src="https://github.com/user-attachments/assets/4bc991c8-197b-4978-8a61-5353fff3ab0d" />


```
==============================================================
LOG FILE        : HDFS\HDFS_2k.log
Total lines     : 2000
Unique templates: 21

--- Top-5 Templates ---
  T-2    |   314 ( 15.7%) | <*> <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem.addStoredBlo
  T-1    |   311 ( 15.6%) | <*> <*> <*> INFO dfs.DataNode$PacketResponder: PacketResponder <*>
  T-3    |   292 ( 14.6%) | <*> <*> <*> INFO dfs.DataNode$PacketResponder: Received block <*>
  T-4    |   292 ( 14.6%) | <*> <*> <*> INFO dfs.DataNode$DataXceiver: Receiving block <*> sr
  T-7    |   263 ( 13.2%) | <*> <*> <*> INFO dfs.FSDataset: Deleting block <*> file <*>

--- Templates spike trong 1 gio gan nhat (ratio>3x) ---
  T-16   | last_hr=9 avg/hr=1.3 ratio=7.2x | 081111 <*> <*> INFO dfs.FSNamesystem: BLOCK* NameSystem

--- New templates (chua xuat hien truoc 1 gio gan nhat) ---
  (khong co template moi)
==============================================================
```

### 4.3 Test Script trên BGL Dataset
<img width="1209" height="462" alt="image" src="https://github.com/user-attachments/assets/f7177296-6140-4c47-8cb1-a9850109d17e" />


```
==============================================================
LOG FILE        : BGL\BGL_2k.log
Total lines     : 2000
Unique templates: 151

--- Top-5 Templates ---
  T-73   |   180 (  9.0%) | - <*> 2005.07.09 <*> <*> <*> RAS KERNEL INFO generating <*>
  T-85   |   121 (  6.0%) | - <*> <*> <*> <*> <*> RAS KERNEL INFO <*> floating point alignmen
  T-2    |   109 (  5.5%) | - <*> <*> <*> <*> <*> RAS KERNEL INFO <*> double-hummer alignment
  T-3    |    92 (  4.6%) | - <*> <*> <*> <*> <*> RAS KERNEL INFO CE sym <*> at <*> mask <*>
  T-77   |    87 (  4.3%) | - <*> 2005.07.13 <*> <*> <*> RAS KERNEL INFO generating <*>

--- Templates spike trong 1 gio gan nhat (ratio>3x) ---
  T-104  | last_hr=1 avg/hr=0.0 ratio=176.8x | - <*> <*> <*> <*> <*> RAS KERNEL INFO ciod: generated

--- New templates (chua xuat hien truoc 1 gio gan nhat) ---
  (khong co template moi)
==============================================================
```


### 4.4 So sánh HDFS vs BGL

| Dataset | Lines | Templates | Templates / 1k lines |
|---|---|---|---|
| HDFS_2k | 2000 | **21** | 10.5 |
| BGL_2k | 2000 | **151** | 75.5 |

**BGL_2k có nhiều template hơn 7.2× so với HDFS_2k.** Nguyên nhân:

**[1] Độ phức tạp cấu trúc log:**
- HDFS chỉ có 2 component chính (DataNode, FSNamesystem), message rất nhất quán, biến chủ yếu là block-ID và IP — Drain3 gộp tốt.
- BGL là supercomputer BlueGene/L với nhiều component: KERNEL, APP, RAS, MPI, BGLMASTER, NET… mỗi component có cú pháp message riêng biệt, không lặp lại nhau.

**[2] Độ đa dạng sự kiện:**
- HDFS: ~5 loại sự kiện cơ bản (block create/receive/delete/replicate/serve), lặp lại theo chu kỳ.
- BGL: 120+ EventId thực tế — parity error, TLB miss, alignment exception, MPI rank failure, scheduler event, network fabric error…

**[3] Biến số trong log message:**
- HDFS: block-ID và IP dễ tách thành `<*>` nhất quán.
- BGL: Node-ID dạng `R02-M1-N0-C:J12-U11`, mã lỗi hex, số đếm lớn — Drain3 khó gộp, sinh thêm nhiều cluster.

**[4] Tỉ lệ anomaly:**
- HDFS: ~4% WARN (80/2000) — exception khi serve block.
- BGL: ~7% non-normal (143/2000) — FATAL + ERROR + WARNING + SEVERE, bao gồm lỗi phần cứng nghiêm trọng (KERNDTLB, KERNSTOR, KERNMNTF…).

**Kết luận:** Template count là proxy tốt cho độ phức tạp của hệ thống. BGL log phản ánh hệ thống phức tạp hơn (supercomputer cluster) so với HDFS (distributed file system). Drain3 với cùng `sim_th=0.5` xử lý tốt cả hai, nhưng BGL cần nhiều cluster hơn để đại diện đúng cho sự đa dạng sự kiện thực tế.

---

## Reflection

### Drain3 parse tốt không?

**Drain3 hoạt động tốt với HDFS log** vì:
- HDFS log có cấu trúc rất nhất quán: `timestamp component level message`
- Các biến như block ID, IP, size đều được `<*>` hóa đúng cách
- 21 template với `sim_th=0.5` phản ánh chính xác các loại sự kiện HDFS thực tế (block creation, transfer, deletion, replication…)

**Hạn chế nhỏ:**
- Với `sim_th=0.7`, Drain3 tạo quá nhiều template (820) do các địa chỉ IP/block ID dạng hex làm nhiễu cây phân tích
- Cần tiền xử lý regex (bỏ timestamp đầu dòng) để Drain3 focus vào phần message thực sự

### Template nào cho insight hữu ích nhất?

| Template | Insight |
|---|---|
| T-2 (addStoredBlock) | Theo dõi quá trình block replication thành công — spike = hệ thống đang recovery hoặc re-balance |
| T-1 (PacketResponder terminating) | DataNode ngừng nhận block — tần suất cao bất thường = DataNode failure |
| T-7 (Deleting block) | Quá trình dọn dẹp block — spike đột ngột = disk space pressure hoặc mis-replication fix |
| T-12 (xuất hiện trong anomaly window) | Template ít gặp nhưng tăng đột biến = dấu hiệu lỗi hệ thống cần điều tra |

### Metric-based (D1) vs Log-based (D2) — Khác gì nhau?

| Tiêu chí | Metric (D1 — CPU/Latency) | Log (D2 — HDFS templates) |
|---|---|---|
| **Dữ liệu** | Chuỗi số liên tục theo thời gian | Văn bản bán cấu trúc, rời rạc |
| **Preprocessing** | Chuẩn hóa, rolling window | Parse → template → count |
| **Anomaly signal** | Giá trị vượt ngưỡng (3σ) hoặc outlier (IF) | Template count spike, new template |
| **Độ trễ** | Gần real-time, liên tục | Phụ thuộc tốc độ parse log |
| **Interpretability** | Khó giải thích "tại sao" anomaly | Template text gợi ý rõ nguyên nhân |
| **Coverage** | Chỉ thấy triệu chứng số | Thấy được root cause dạng text |
| **Kết hợp** | Metric anomaly ↔ Log spike xảy ra cùng lúc → độ tin cậy cao hơn nhiều | |

**Kết luận:** Log-based anomaly detection bổ sung cho metric-based: khi metric phát hiện anomaly, log cho biết **cái gì** đang xảy ra bên trong hệ thống.

## Knowledge Check (viết tay)

1. Giải thích Drain3 parse tree hoạt động thế nào (vẽ sơ đồ đơn giản).
<img width="1536" height="2048" alt="image" src="https://github.com/user-attachments/assets/85ab1719-cf4a-49c0-87dd-677e24f6210d" />

2. Tại sao cần log parsing thay vì grep — cho ví dụ cụ thể.
<img width="1536" height="2048" alt="image" src="https://github.com/user-attachments/assets/47d52228-6cb5-4ec3-b1a6-b30b6f882080" />

3. Template count time series là gì, tại sao dùng nó để detect anomaly.
<img width="1536" height="2048" alt="image" src="https://github.com/user-attachments/assets/c651fbfa-a49e-4053-9e53-0e0720624b52" />

4. New template detection: tại sao template mới là signal quan trọng.
<img width="1536" height="2048" alt="image" src="https://github.com/user-attachments/assets/d8cff07f-d97f-4557-8312-27fbf70eb0dd" />

5. Metric cho biết gì, log cho biết gì, kết hợp 2 cái thì được gì.
<img width="1536" height="2048" alt="image" src="https://github.com/user-attachments/assets/5367de10-aacb-4983-ae85-9def1b652ff8" />


