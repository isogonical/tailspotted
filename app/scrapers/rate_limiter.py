import asyncio
import time

import redis.asyncio as redis

from app.config import settings


class RateLimiter:
    """Redis sorted-set sliding window rate limiter."""

    def __init__(self, domain: str, max_requests: int, window_seconds: int):
        self.key = f"ratelimit:{domain}"
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis: redis.Redis | None = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self) -> None:
        """Wait until a request slot is available, then consume it."""
        r = await self._get_redis()
        while True:
            now = time.time()
            window_start = now - self.window_seconds

            pipe = r.pipeline()
            pipe.zremrangebyscore(self.key, 0, window_start)
            pipe.zcard(self.key)
            results = await pipe.execute()
            count = results[1]

            if count < self.max_requests:
                await r.zadd(self.key, {str(now): now})
                await r.expire(self.key, self.window_seconds + 10)
                return

            await asyncio.sleep(1.0)
