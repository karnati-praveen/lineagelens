from __future__ import annotations

import time

from app.core.rate_limit import RateLimitDecision


class RedisRateLimiter:
    """Redis-backed sliding-window rate limiter using sorted sets.

    Safe for multi-replica deployments. Each backend instance shares state through Redis.

    Requirements: pip install redis[asyncio]
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def acheck(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        safe_limit = max(1, limit)
        safe_window = max(1, window_seconds)
        window_start = now - safe_window
        prefixed = f"rl:{key}"

        # Atomic: prune expired entries, then read current count.
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.zremrangebyscore(prefixed, "-inf", window_start)
            pipe.zcard(prefixed)
            results = await pipe.execute()

        count = int(results[1])

        if count >= safe_limit:
            oldest_entries = await self._client.zrange(prefixed, 0, 0, withscores=True)
            if oldest_entries:
                oldest_time = float(oldest_entries[0][1])
                retry_after = max(1, int((oldest_time + safe_window) - now))
            else:
                retry_after = safe_window
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=retry_after,
                remaining=0,
                limit=safe_limit,
            )

        # Record this request and set key expiry.
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.zadd(prefixed, {str(now): now})
            pipe.expire(prefixed, safe_window + 1)
            await pipe.execute()

        remaining = max(0, safe_limit - count - 1)
        return RateLimitDecision(
            allowed=True,
            retry_after_seconds=0,
            remaining=remaining,
            limit=safe_limit,
        )

    async def close(self) -> None:
        await self._client.aclose()
