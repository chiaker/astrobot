from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from astrobot.config import get_settings


@lru_cache
def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.redis_url, decode_responses=True)
