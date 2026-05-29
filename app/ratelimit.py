"""
Redis-backed rate limiting for the public Developer API.

Uses a fixed-window counter (INCR + EXPIRE) in Redis so limits hold across all
worker processes/instances. When Redis is unavailable it degrades to a process-
local in-memory window so a Redis outage fails *open* for availability rather
than locking every client out. The limiter core is sync-testable; the FastAPI
dependency layer is thin.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after: int   # seconds until the window resets
    backend: str       # "redis" | "memory"


class InMemoryWindow:
    """Process-local fixed-window counter (fallback / tests / single-node dev)."""

    def __init__(self) -> None:
        self._hits: Dict[str, Tuple[int, float]] = {}   # key -> (count, window_start)

    def incr(self, key: str, window: int, now: Optional[float] = None) -> Tuple[int, float]:
        now = now if now is not None else time.time()
        count, start = self._hits.get(key, (0, now))
        if now - start >= window:
            count, start = 0, now
        count += 1
        self._hits[key] = (count, start)
        return count, start


class RateLimiter:
    """
    Fixed-window limiter.

    Parameters
    ----------
    redis_client
        An *async* redis client (``redis.asyncio.Redis``) or ``None`` to force
        the in-memory fallback.
    """

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self._memory = InMemoryWindow()

    async def check(self, key: str, limit: int, window: int) -> RateLimitResult:
        if limit < 0:  # negative limit == unlimited (e.g. enterprise plan)
            return RateLimitResult(True, limit, -1, window, "unlimited")

        if self.redis is not None:
            try:
                return await self._check_redis(key, limit, window)
            except Exception:
                # Redis hiccup -> fail open via memory window
                pass
        return self._check_memory(key, limit, window)

    async def _check_redis(self, key: str, limit: int, window: int) -> RateLimitResult:
        redis_key = f"ratelimit:{key}"
        count = await self.redis.incr(redis_key)
        if count == 1:
            await self.redis.expire(redis_key, window)
        ttl = await self.redis.ttl(redis_key)
        ttl = window if (ttl is None or ttl < 0) else ttl
        remaining = max(0, limit - count)
        return RateLimitResult(count <= limit, limit, remaining, ttl, "redis")

    def _check_memory(self, key: str, limit: int, window: int) -> RateLimitResult:
        count, start = self._memory.incr(key, window)
        reset_after = max(0, int(window - (time.time() - start)))
        remaining = max(0, limit - count)
        return RateLimitResult(count <= limit, limit, remaining, reset_after, "memory")
