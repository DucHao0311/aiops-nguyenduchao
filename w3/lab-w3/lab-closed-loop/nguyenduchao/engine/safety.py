"""
engine/safety.py — Blast-radius guard and circuit breaker.

BlastRadiusGuard:
  - Global: max N actions/minute across all services.
  - Per-service: max M restarts/hour per individual service.
  check()  → (ok: bool, reason: str) — does NOT record; call record() on execute.
  record() → stamp timestamp for both global and per-service windows.

CircuitBreaker:
  - Opens after N consecutive verify failures.
  - Manual reset only: operator restarts the orchestrator process.
"""

import time
from collections import defaultdict, deque

from engine.logger import JsonLogger

log = JsonLogger("safety")


class BlastRadiusGuard:
    """Enforce per-minute global and per-service-per-hour action limits."""

    def __init__(self, max_per_minute: int, max_restarts_per_hour: int):
        self._max_per_minute = max_per_minute
        self._max_restarts_per_hour = max_restarts_per_hour
        self._global_window: deque[float] = deque()
        self._service_window: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, window: deque, horizon: float) -> None:
        while window and window[0] < horizon:
            window.popleft()

    def remaining_global(self) -> int:
        now = time.time()
        self._prune(self._global_window, now - 60)
        return max(0, self._max_per_minute - len(self._global_window))

    def check(self, service: str) -> tuple[bool, str]:
        now = time.time()
        self._prune(self._global_window, now - 60)
        self._prune(self._service_window[service], now - 3600)

        if len(self._global_window) >= self._max_per_minute:
            return False, (
                f"global actions/min limit ({self._max_per_minute}) reached — "
                f"current window has {len(self._global_window)} actions"
            )
        if len(self._service_window[service]) >= self._max_restarts_per_hour:
            return False, (
                f"restarts/hour limit ({self._max_restarts_per_hour}) for "
                f"{service} — already {len(self._service_window[service])} this hour"
            )
        return True, "ok"

    def record(self, service: str) -> None:
        now = time.time()
        self._global_window.append(now)
        self._service_window[service].append(now)


class CircuitBreaker:
    """Halt automation after N consecutive verify failures (manual reset)."""

    def __init__(self, threshold: int):
        self._threshold = threshold
        self._failures = 0
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def failure_count(self) -> int:
        return self._failures

    def record_failure(self) -> None:
        self._failures += 1
        log.warning(
            "CIRCUIT_BREAKER_FAILURE",
            consecutive_failures=self._failures,
            threshold=self._threshold,
        )
        if self._failures >= self._threshold:
            self._open = True
            log.error(
                "CIRCUIT_BREAKER_HALT",
                consecutive_failures=self._failures,
                threshold=self._threshold,
                message="Circuit OPEN — automation halted. Manual restart required.",
            )

    def record_success(self) -> None:
        if self._failures > 0:
            log.info("CIRCUIT_BREAKER_RESET", previous_failures=self._failures)
        self._failures = 0
