"""Redis-backed fixed-window rate limiter, keyed per API key.

Copyright (c) 2026 Jay Vallea. All Rights Reserved.
"""

from __future__ import annotations

import time

import redis.asyncio as redis


class RateLimiter:
    def __init__(self, client: redis.Redis, limit_per_minute: int, prefix: str = "rl:") -> None:
        self.redis = client
        self.limit = limit_per_minute
        self.prefix = prefix

    async def hit(self, subject: str) -> tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after_seconds)."""
        now = int(time.time())
        window = now // 60
        key = f"{self.prefix}{subject}:{window}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, 120)
            count, _ = await pipe.execute()
        remaining = max(0, self.limit - int(count))
        retry_after = 60 - (now % 60)
        return int(count) <= self.limit, remaining, retry_after
