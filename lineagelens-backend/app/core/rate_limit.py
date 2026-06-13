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

    async def acheck(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        # Intentionally delegates to the synchronous .check() without an executor.
        # This is safe because InMemoryRateLimiter.check() is fast (in-memory, no I/O)
        # and the async def is only needed for duck-typing compatibility with the
        # RedisRateLimiter interface (which IS truly async).
        return self.check(key=key, limit=limit, window_seconds=window_seconds)

    def _compact_if_necessary(self, *, now: float, window_seconds: int) -> None:
        if len(self._events) <= self._max_tracked_keys:
            return

        stale_keys: list[str] = []
        for key, bucket in self._events.items():
            self._prune(bucket, now, window_seconds)
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


def _parse_trusted_proxy_ips(trusted_proxy_ips: str | None) -> set[str]:
    raw = (trusted_proxy_ips or "").strip()
    if not raw:
        return set()
    return {entry.strip() for entry in raw.split(",") if entry.strip()}


def effective_client_ip(
    peer_host: str | None,
    forwarded_for_header: str,
    real_ip_header: str,
    trusted_proxy_ips: str | None = None,
) -> str:
    """Return the real client IP, honoring forwarded headers only from allowlisted proxies.

    Walks the X-Forwarded-For chain right-to-left (i.e. from the last hop added
    by a trusted proxy towards the original client) and returns the first entry
    that is NOT in trusted_proxy_ips.  This prevents a spoofed left-most entry
    from being used as the "real" IP: an attacker can forge any value they like
    in the leftmost XFF position, but they cannot forge the rightmost entry
    because that is appended by the trusted proxy closest to us.
    """
    trusted_ips = _parse_trusted_proxy_ips(trusted_proxy_ips)
    normalized_peer = (peer_host or "").strip()

    # If the direct peer is not a trusted proxy, use it as-is.
    if normalized_peer not in trusted_ips:
        return client_identifier(peer_host)

    # Peer is trusted: walk XFF right-to-left for the first untrusted entry.
    if forwarded_for_header.strip():
        chain = [e.strip() for e in forwarded_for_header.split(",") if e.strip()]
        for ip in reversed(chain):
            if ip not in trusted_ips:
                return client_identifier(ip)

    # Fall back to X-Real-IP, then to the direct peer.
    real_ip = real_ip_header.strip()
    if real_ip:
        return client_identifier(real_ip)
    return client_identifier(peer_host)
