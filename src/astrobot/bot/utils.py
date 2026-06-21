from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import BirthProfile, User
from astrobot.redis_client import get_redis

log = structlog.get_logger(__name__)


async def need_profile(
    message: Message,
    session: AsyncSession,
    user: User,
) -> BirthProfile | None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await message.answer(
            "🌙 Нам надо познакомиться — мне нужны твои дата, время и место рождения. "
            "Нажми /start, чтобы начать."
        )
    return profile


@asynccontextmanager
async def user_llm_lock(user_id: int, ttl: int = 120) -> AsyncIterator[bool]:
    """Per-user mutual-exclusion lock for expensive LLM operations.

    Serializes a single user's concurrent requests so the check→LLM→consume
    flow can't be raced (aiogram dispatches updates concurrently). Yields True
    when the lock was acquired, False when another request is already running.
    Auto-expires after `ttl` seconds so a crashed handler can't wedge the user.
    If Redis is unavailable we yield True (don't block real usage).
    """
    redis = get_redis()
    key = f"llm:lock:{user_id}"
    try:
        acquired = bool(await redis.set(key, "1", ex=ttl, nx=True))
    except Exception as e:
        log.warning("llm_lock_redis_error", user_id=user_id, error=str(e))
        yield True
        return
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            await redis.delete(key)
        except Exception:
            pass


async def rate_limit_ok(key: str, limit: int, window_seconds: int) -> bool:
    """Sliding fixed-window counter. Returns True while the call count for `key`
    stays within `limit` over `window_seconds`. Fail-open if Redis is down."""
    redis = get_redis()
    try:
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, window_seconds)
        return n <= limit
    except Exception:
        return True
