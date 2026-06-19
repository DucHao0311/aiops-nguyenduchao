"""
engine/logger.py — Structured JSON logger for the closed-loop orchestrator.

Every log record has: ts, level, event_type + arbitrary kwargs.
Also writes to audit_log.jsonl for Promtail → Loki ingestion.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class JsonLogger:
    """Emit structured JSON log records to stdout AND to audit_log.jsonl."""

    def __init__(self, name: str):
        self._name = name
        self._lock = threading.Lock()

        # Audit log path: env var takes priority (used when running in Docker
        # with the audit_logs volume mounted at /audit).
        audit_path = os.environ.get("AUDIT_LOG_PATH", "audit_log.jsonl")
        self._audit_path = Path(audit_path)

    def _emit(self, level: str, event_type: str, **kwargs):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event_type": event_type,
            "logger": self._name,
            **kwargs,
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)

        # Append to audit log file (thread-safe)
        try:
            with self._lock:
                with open(self._audit_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass  # never let logging crash the orchestrator

    def info(self, event_type: str, **kwargs):
        self._emit("INFO", event_type, **kwargs)

    def warning(self, event_type: str, **kwargs):
        self._emit("WARNING", event_type, **kwargs)

    def error(self, event_type: str, **kwargs):
        self._emit("ERROR", event_type, **kwargs)
