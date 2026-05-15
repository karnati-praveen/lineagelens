from __future__ import annotations

import secrets
import time

from app.core.rate_limit import RateLimitDecision

# Atomically: prune expired entries → check count → conditionally add + set expiry.
# Eliminates the race between the count read and the zadd in a two-pipeline approach.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window_start = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local expiry = tonumber(ARGV[4])
local member = ARGV[5]

redis.call('zremrangebyscore', key, '-inf', window_start)
local count = redis.call('zcard', key)

if count >= limit then
    local oldest = redis.call('zrange', key, 0, 0, 'WITHSCORES')
    if #oldest >= 2 then
        return {count, 0, oldest[2]}
    end
    return {count, 0, tostring(now)}
end

redis.call('zadd', key, now, member)
redis.call('expire', key, expiry)
return {count + 1, 1, '0'}
"""


class RedisRateLimiter:
    """Redis-backed sliding-window rate limiter using sorted sets.

    Safe for multi-replica deployments. Each backend instance shares state through Redis.

    Requirements: pip install redis[asyncio]
    """

    def __init__(self, redis_url: str) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(redis_url, decode_responses=True)
        self._script = self._client.register_script(_SLIDING_WINDOW_LUA)

    async def acheck(self, *, key: str, limit: int, window_seconds: int) -> RateLimitDecision:
        now = time.time()
        safe_limit = max(1, limit)
        safe_window = max(1, window_seconds)
        window_start = now - safe_window
        prefixed = f"rl:{key}"
        member = f"{now}:{secrets.token_hex(8)}"

        result = await self._script(
            keys=[prefixed],
            args=[window_start, now, safe_limit, safe_window + 1, member],
        )

        count = int(result[0])
        allowed = bool(result[1])

        if not allowed:
            try:
                oldest_time = float(result[2])
                retry_after = max(1, int((oldest_time + safe_window) - now))
            except (ValueError, TypeError):
                retry_after = safe_window
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=retry_after,
                remaining=0,
                limit=safe_limit,
            )

        remaining = max(0, safe_limit - count)
        return RateLimitDecision(
            allowed=True,
            retry_after_seconds=0,
            remaining=remaining,
            limit=safe_limit,
        )

    async def close(self) -> None:
        await self._client.aclose()
