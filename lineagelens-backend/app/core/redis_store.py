from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RedisStore:
    """Async key-value store: Redis-backed when a client is provided, in-process dict otherwise.

    This lets export_jobs and webhook configs survive across multiple backend replicas
    when REDIS_URL is configured. Without Redis, behaviour is identical to the original
    in-process dict.
    """

    def __init__(self, redis_client: Any | None = None, prefix: str = "ll:") -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._mem: dict[str, Any] = {}

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get(self, key: str) -> Any | None:
        if self._redis is None:
            return self._mem.get(key)
        raw = await self._redis.get(self._k(key))
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int = 7200) -> None:
        if self._redis is None:
            self._mem[key] = value
            return
        await self._redis.setex(self._k(key), ttl, json.dumps(value, default=str))

    async def delete(self, key: str) -> None:
        if self._redis is None:
            self._mem.pop(key, None)
            return
        await self._redis.delete(self._k(key))

    def local_items(self) -> list[tuple[str, Any]]:
        """Iterate in-memory entries — only valid when Redis is NOT in use."""
        return list(self._mem.items())

    def local_pop(self, key: str) -> None:
        self._mem.pop(key, None)

    @property
    def uses_redis(self) -> bool:
        return self._redis is not None
