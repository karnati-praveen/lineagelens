from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock


@dataclass(slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int
    remaining: int
    limit: int


class InMemoryRateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Notes:
    - State is process-local. In multi-replica deployments, use a shared limiter
      backend (e.g. Redis) instead.
    """

    def __init__(self, max_tracked_keys: int = 50000) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._max_tracked_keys = max(1000, max_tracked_keys)

    def check(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        safe_limit = max(1, limit)
        safe_window = max(1, window_seconds)

        with self._lock:
            bucket = self._events[key]
            self._prune(bucket, now, safe_window)

            if len(bucket) >= safe_limit:
                oldest = bucket[0]
                retry_after = max(1, int((oldest + safe_window) - now))
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=retry_after,
                    remaining=0,
                    limit=safe_limit,
                )

            bucket.append(now)
            remaining = max(0, safe_limit - len(bucket))
            self._compact_if_necessary(now=now, window_seconds=safe_window)

            return RateLimitDecision(
                allowed=True,
                retry_after_seconds=0,
                remaining=remaining,
                limit=safe_limit,
            )

    def _prune(self, bucket: deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while bucket and bucket[0] <= threshold:
            bucket.popleft()

    def _compact_if_necessary(self, *, now: float, window_seconds: int) -> None:
        if len(self._events) <= self._max_tracked_keys:
            return

        threshold = now - window_seconds

        stale_keys: list[str] = []
        for key, bucket in self._events.items():
            while bucket and bucket[0] <= threshold:
                bucket.popleft()
            if not bucket:
                stale_keys.append(key)

        for key in stale_keys:
            self._events.pop(key, None)


def client_identifier(
    client_host: str | None,
    *,
    fallback: str = "unknown",
) -> str:
    host = (client_host or "").strip()
    return host if host else fallback
