"""
pipeline.py — Mock Streaming Pipeline for AIOps Anomaly Detection
Use case: Payment Service Anomaly Detection (machine temperature as proxy metric)

Architecture:
  CSV file → Producer Thread → queue.Queue (fake Kafka) → Consumer Thread → features.parquet

Feature engineering (stream-style):
  - rolling_mean_12  : 60-min rolling mean  (12 × 5-min windows)
  - rolling_std_12   : 60-min rolling std
  - rolling_mean_60  : 300-min rolling mean (60 × 5-min windows)
  - rate_of_change   : (current - previous) / previous
  - z_score          : (value - rolling_mean_12) / (rolling_std_12 + ε)

Run:
    uv run python pipeline.py
    # or: python pipeline.py
"""

import queue
import threading
import time
import csv
import json
import os
from collections import deque
from pathlib import Path

import pandas as pd
import numpy as np

# Config 
CSV_PATH    = Path("realKnownCause/machine_temperature_system_failure.csv")
OUTPUT_PARQUET = Path("features.parquet")
OUTPUT_EVENTS  = Path("events.jsonl")       # fake Kafka log

WINDOW_SHORT = 12    # 12 × 5 min = 60 min
WINDOW_LONG  = 60    # 60 × 5 min = 300 min
PRODUCER_DELAY = 0.0 # set > 0 to simulate real-time (e.g. 0.001)
QUEUE_MAXSIZE  = 1000

EPS = 1e-9  # avoid division by zero

# Shared queue (fake Kafka topic)
event_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAXSIZE)

# Producer 
def producer(csv_path: Path, out_queue: queue.Queue) -> None:
    """
    Reads CSV row-by-row and puts each record into the queue.
    Also appends raw events to events.jsonl (simulates Kafka topic write).
    """
    print(f"[Producer] Starting — reading {csv_path}")
    count = 0

    with open(csv_path, newline="", encoding="utf-8") as f, \
         open(OUTPUT_EVENTS, "w", encoding="utf-8") as jf:

        reader = csv.DictReader(f)
        for row in reader:
            event = {
                "timestamp": row["timestamp"],
                "value": float(row["value"]),
            }
            # Write to events.jsonl  (fake Kafka produce)
            jf.write(json.dumps(event) + "\n")

            # Put into in-process queue (fake Kafka consume lag = 0)
            out_queue.put(event)
            count += 1

            if PRODUCER_DELAY:
                time.sleep(PRODUCER_DELAY)

    # Sentinel value signals consumer to stop
    out_queue.put(None)
    print(f"[Producer] Done — emitted {count} events")


# Consumer / Feature Extractor 
def consumer(in_queue: queue.Queue, output_path: Path) -> None:
    """
    Consumes events from queue, maintains rolling buffers,
    extracts features on each event, accumulates results, writes parquet.
    """
    print("[Consumer] Starting — waiting for events…")

    buf_short: deque = deque(maxlen=WINDOW_SHORT)  # last 60 min
    buf_long:  deque = deque(maxlen=WINDOW_LONG)   # last 300 min

    records = []
    prev_value = None
    count = 0

    while True:
        event = in_queue.get()
        if event is None:          # sentinel → done
            break

        ts    = event["timestamp"]
        value = event["value"]

        buf_short.append(value)
        buf_long.append(value)

        arr_short = np.array(buf_short)
        arr_long  = np.array(buf_long)

        rolling_mean_12 = float(np.mean(arr_short))
        rolling_std_12  = float(np.std(arr_short))
        rolling_mean_60 = float(np.mean(arr_long))
        rolling_std_60  = float(np.std(arr_long))

        rate_of_change = (
            (value - prev_value) / (abs(prev_value) + EPS)
            if prev_value is not None else 0.0
        )

        z_score = (value - rolling_mean_12) / (rolling_std_12 + EPS)

        records.append({
            "timestamp":      ts,
            "value":          value,
            "rolling_mean_12": rolling_mean_12,
            "rolling_std_12":  rolling_std_12,
            "rolling_mean_60": rolling_mean_60,
            "rolling_std_60":  rolling_std_60,
            "rate_of_change":  rate_of_change,
            "z_score":         z_score,
        })

        prev_value = value
        count += 1

        if count % 5000 == 0:
            print(f"[Consumer] Processed {count} events…")

    # Write output
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.to_parquet(output_path, index=False)

    print(f"[Consumer] Done — {count} events → {output_path}")
    print(f"\n{'─'*55}")
    print(df[["timestamp","value","rolling_mean_12","z_score","rate_of_change"]].tail(5).to_string())
    print(f"\nSchema:\n{df.dtypes}")
    print(f"\nOutput size: {output_path.stat().st_size / 1024:.1f} KB")


def main() -> None:
    print("=" * 55)
    print("  AIOps Mock Streaming Pipeline")
    print("  Use case: Payment Service Anomaly Detection")
    print("=" * 55)

    t0 = time.time()

    # Run producer + consumer as threads (simulates Kafka producer/consumer)
    prod_thread = threading.Thread(target=producer, args=(CSV_PATH, event_queue), daemon=True)
    cons_thread = threading.Thread(target=consumer, args=(event_queue, OUTPUT_PARQUET))

    prod_thread.start()
    cons_thread.start()

    prod_thread.join()
    cons_thread.join()

    elapsed = time.time() - t0
    print(f"\n✓ Pipeline finished in {elapsed:.2f}s")
    print(f"  Features : {OUTPUT_PARQUET}")
    print(f"  Raw events: {OUTPUT_EVENTS}")


if __name__ == "__main__":
    main()
