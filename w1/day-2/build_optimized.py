import json

nb_path = r'c:\Users\ASUS\Documents\aiops-nguyenduchao\w1\day-2\assignment.ipynb'
nb = json.load(open(nb_path, encoding='utf-8'))

# ── Cell 1: Setup (fix LOG_FILE path separator) ───────────────────────────────
cell1_code = r"""import os
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.ensemble import IsolationForest
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

os.makedirs("results", exist_ok=True)
LOG_FILE = os.path.join("HDFS", "HDFS_2k.log")
OUTPUT_DIR = "results"
print("Khởi tạo môi trường thành công!")
"""

# ── Cell 3: Load + đếm dòng (giữ nguyên logic, không thay đổi) ───────────────
cell3_code = r"""# Load log file, đếm tổng số dòng
if not os.path.exists(LOG_FILE):
    raise FileNotFoundError(f"Không tìm thấy file {LOG_FILE}")

with open(LOG_FILE, "r", encoding="utf-8") as f:
    total_lines = sum(1 for _ in f)

print(f"Tổng số dòng log đọc được: {total_lines}")
"""

# ── Cell 4: Parse với Drain3 — FIX timestamp + optimal params ────────────────
cell4_code = r"""# ============================================================
# PHASE 1 — PARSE LOG VỚI DRAIN3
# Parameters tối ưu:
#   drain_sim_th = 0.5   → cân bằng, tránh over-split (0.7 → 820 templates)
#   drain_depth  = 5     → sâu hơn default 4, phân biệt tốt hơn
#   max_clusters = 1024  → đủ lớn cho HDFS
# FIX: timestamp HDFS format là YYmmdd HHMMSS (2-digit year = 20xx)
# ============================================================

config = TemplateMinerConfig()
config.drain_sim_th = 0.5
config.drain_depth  = 5          # tăng từ 4 → 5 để split tốt hơn
config.drain_max_clusters = 1024

miner = TemplateMiner(config=config)
parsed_data = []

# Regex chuẩn cho HDFS: YYmmdd HHMMSS
TS_PATTERN = re.compile(r"^(\d{6})\s(\d{6})")

with open(LOG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        m = TS_PATTERN.match(line)
        if m:
            # FIX: prefix '20' để ra 2008, bukan 2081
            ts_str = "20" + m.group(1) + " " + m.group(2)
            try:
                timestamp = pd.to_datetime(ts_str, format="%Y%m%d %H%M%S")
            except Exception:
                timestamp = pd.NaT
        else:
            timestamp = pd.NaT

        result = miner.add_log_message(line)
        parsed_data.append({
            "timestamp":   timestamp,
            "template_id": f"T-{result['cluster_id']}",
            "message":     line
        })

df_logs = pd.DataFrame(parsed_data)

# Map template_id → template text (lấy final state sau khi drain hội tụ)
id_to_template = {f"T-{c.cluster_id}": c.get_template() for c in miner.drain.clusters}
df_logs["template"] = df_logs["template_id"].map(id_to_template)

# Tổng hợp templates
df_templates = (df_logs.groupby(["template_id", "template"])
                       .size()
                       .reset_index(name="count")
                       .sort_values("count", ascending=False))

df_templates.head(10).to_csv("results/top_templates.csv", index=False)

print(f"Số template độc nhất: {len(df_templates)}")
print(f"Khoảng thời gian: {df_logs['timestamp'].min()} → {df_logs['timestamp'].max()}")
print(f"\n=== TOP 5 TEMPLATES ===")
print(df_templates.head(5).to_string(index=False))
"""

# ── Cell 5: Tuning (giữ nguyên) ───────────────────────────────────────────────
cell5_code = r"""def tune_drain(log_file, thresholds=[0.3, 0.5, 0.7]):
    lines = []
    with open(log_file, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                lines.append(ln)

    results = {}
    for th in thresholds:
        cfg = TemplateMinerConfig()
        cfg.drain_sim_th = th
        cfg.drain_depth  = 5
        m = TemplateMiner(config=cfg)
        for ln in lines:
            m.add_log_message(ln)
        n = len(m.drain.clusters)
        results[th] = n
        print(f"  drain_sim_th={th} → {n:4d} templates")
    return results

print("=== TUNING drain_sim_th ===")
tuning_results = tune_drain(LOG_FILE)
print("\nChọn: sim_th=0.5 — cân bằng giữa chi tiết và tổng quát nhất.")
"""

# ── Cell 7: Phase 2 — Time series + Anomaly Detection (FULL REWRITE) ─────────
cell7_code = r"""# ============================================================
# PHASE 2 — ANOMALY DETECTION TRÊN LOG
# Cải tiến so với version cũ:
#   1. FIX timestamp → trục thời gian đúng (2008, không phải 2081)
#   2. Window 1 phút thay vì 5 phút → bắt spike ngắn hơn
#   3. Dùng 3-sigma trên tổng log/window (univariate) thay vì
#      Isolation Forest multi-dim → ít false positive hơn
#   4. Thêm WARN-rate signal như feature phụ
# ============================================================

WINDOW = "1min"   # window 1 phút cho HDFS_2k (span ~38h)

# --- 2.1 Build time series ---
ts_total = (df_logs.set_index("timestamp")
                   .resample(WINDOW)["template_id"]
                   .count()
                   .rename("total"))

# WARN-level signal: dùng template chứa "exception" hoặc "WARN"
warn_mask = df_logs["message"].str.contains("WARN|exception|Exception", regex=True, na=False)
ts_warn = (df_logs[warn_mask]
              .set_index("timestamp")
              .resample(WINDOW)["template_id"]
              .count()
              .rename("warn_count")
              .reindex(ts_total.index, fill_value=0))

ts_df = pd.concat([ts_total, ts_warn], axis=1)

# Loại bỏ window rỗng (0 log) khỏi baseline — đây là "im lặng", không phải bình thường
active = ts_df[ts_df["total"] > 0]

# --- 2.2 3-sigma thresholding trên tổng log ---
mu    = active["total"].mean()
sigma = active["total"].std()
threshold_high = mu + 3 * sigma
threshold_low  = mu - 2 * sigma  # dip bất thường (drop đột ngột)

print(f"Window: {WINDOW}")
print(f"Active windows: {len(active)}")
print(f"Mean log/window: {mu:.2f}  |  Std: {sigma:.2f}")
print(f"3σ upper threshold: {threshold_high:.1f}")
print(f"2σ lower threshold: {threshold_low:.1f}")

# Flag anomaly: spike lên cao (>3σ) HOẶC drop thấp (<2σ dưới, nếu đã từng có traffic)
ts_df["anomaly_spike"] = (ts_df["total"] > threshold_high).astype(int)
ts_df["anomaly_drop"]  = ((ts_df["total"] < max(threshold_low, 1)) &
                           (ts_df["total"] > 0)).astype(int)
ts_df["anomaly"]       = ((ts_df["anomaly_spike"] == 1) |
                           (ts_df["anomaly_drop"] == 1)).astype(int)

n_anomaly = ts_df["anomaly"].sum()
print(f"\nSố window bất thường: {n_anomaly}")

# --- 2.3 Template spike analysis tại anomaly windows ---
ts_template = (df_logs.set_index("timestamp")
                      .groupby([pd.Grouper(freq=WINDOW), "template_id"])
                      .size()
                      .unstack(fill_value=0))

anomaly_times = ts_df[ts_df["anomaly"] == 1].index
print("\n=== TOP ANOMALY WINDOWS ===")
for t in anomaly_times[:5]:
    total_cnt = ts_df.loc[t, "total"]
    warn_cnt  = ts_df.loc[t, "warn_count"]
    if t in ts_template.index:
        top_tid = ts_template.loc[t].idxmax()
        top_cnt = ts_template.loc[t].max()
        tmpl    = id_to_template.get(top_tid, "?")[:70]
    else:
        top_tid, top_cnt, tmpl = "?", 0, "?"
    kind = "SPIKE" if ts_df.loc[t, "anomaly_spike"] else "DROP"
    print(f"  [{kind}] {t} | total={total_cnt} warn={warn_cnt} | top={top_tid}({top_cnt}) | {tmpl}")

# --- 2.4 New template detection ---
# Template mới = chỉ xuất hiện sau nửa sau của log
half_idx = len(df_logs) // 2
first_half_tids = set(df_logs.iloc[:half_idx]["template_id"])
second_half_tids = set(df_logs.iloc[half_idx:]["template_id"])
new_tids = second_half_tids - first_half_tids
print(f"\nTemplate MỚI xuất hiện ở nửa sau dataset: {new_tids if new_tids else 'Không có'}")
"""

# ── Cell 8: Plot (FULL REWRITE — đẹp hơn, đúng hơn) ─────────────────────────
cell8_code = r"""# ============================================================
# PHASE 2 — VISUALISATION (cải tiến)
# ============================================================

fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
fig.suptitle("HDFS Log — Template Count Time Series & Anomaly Detection\n(3σ thresholding, window=1min)", fontsize=13)

# --- Plot 1: total log count + anomaly markers ---
ax1 = axes[0]
ax1.plot(ts_df.index, ts_df["total"], color="#1f77b4", linewidth=0.9, label="Log count / window")
ax1.axhline(threshold_high, color="orange", linestyle="--", linewidth=1.2, label=f"3σ upper = {threshold_high:.1f}")

spike_mask = ts_df["anomaly_spike"] == 1
drop_mask  = ts_df["anomaly_drop"]  == 1

ax1.scatter(ts_df.index[spike_mask], ts_df["total"][spike_mask],
            color="red", zorder=5, s=50, label="Spike anomaly")
ax1.scatter(ts_df.index[drop_mask], ts_df["total"][drop_mask],
            color="purple", zorder=5, s=50, marker="v", label="Drop anomaly")

ax1.set_ylabel("Số log / window")
ax1.legend(fontsize=8)
ax1.grid(True, linestyle="--", alpha=0.5)

# --- Plot 2: WARN count per window ---
ax2 = axes[1]
ax2.bar(ts_df.index, ts_df["warn_count"], width=0.0005,
        color="tomato", alpha=0.8, label="WARN log count")
ax2.set_ylabel("WARN / window")
ax2.set_xlabel("Thời gian")
ax2.legend(fontsize=8)
ax2.grid(True, linestyle="--", alpha=0.5)

ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right", fontsize=7)

plt.tight_layout()
plt.savefig("results/hdfs_anomaly_detection.png", dpi=120, bbox_inches="tight")
plt.show()
print("Plot saved to results/hdfs_anomaly_detection.png")
"""

# ─── Rebuild cells ────────────────────────────────────────────────────────────
def make_code_cell(src, cid=None):
    import random
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cid or f"c{random.randint(100000,999999)}",
        "metadata": {},
        "outputs": [],
        "source": [src]
    }

def make_md_cell(src):
    import random
    return {
        "cell_type": "markdown",
        "id": f"m{random.randint(100000,999999)}",
        "metadata": {},
        "source": [src]
    }

# Replace cells by index (keep cell 0 = title, keep cells 9-14 = Phase3/4)
nb['cells'][1] = make_code_cell(cell1_code, "cell_setup")
# cell 2 = "Phase 1" markdown, keep
nb['cells'][3] = make_code_cell(cell3_code, "cell_load")
nb['cells'][4] = make_code_cell(cell4_code, "cell_parse")
nb['cells'][5] = make_code_cell(cell5_code, "cell_tune")
# cell 6 = "Phase 2" markdown, keep
nb['cells'][7] = make_code_cell(cell7_code, "cell_anomaly")
nb['cells'][8] = make_code_cell(cell8_code, "cell_plot")

with open(nb_path, 'w', encoding='utf-8') as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print("Notebook updated successfully.")
for i, c in enumerate(nb['cells']):
    src = ''.join(c['source'])[:60].replace('\n',' ')
    print(f"  [{i:2d}] {c['cell_type']:8s} | {src}")
