from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import User
from astrobot.db.session import get_sessionmaker
from astrobot.metrics import DUPLICATE_UPDATES_TOTAL
from astrobot.redis_client import get_redis

log = structlog.get_logger(__name__)


class UpdateDedupeMiddleware(BaseMiddleware):
    """Skip updates we've already processed.

    Telegram retries webhooks on timeout, so the same update_id can hit us
    multiple times. With Redis we keep a 1-hour seen-set and drop duplicates.
    """

    TTL_SECONDS = 60 * 60

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        redis = get_redis()
        key = f"update:seen:{event.update_id}"
        try:
            created = await redis.set(key, "1", ex=self.TTL_SECONDS, nx=True)
        except Exception as e:
            log.warning("dedupe_redis_error", error=str(e))
            return await handler(event, data)

        if not created:
            DUPLICATE_UPDATES_TOTAL.inc()
            log.info("duplicate_update_skipped", update_id=event.update_id)
            return None

        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with get_sessionmaker()() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Get-or-create the application User row for the Telegram user; inject as 'user'."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session: AsyncSession = data["session"]
        if tg_user is None:
            return await handler(event, data)

        user = await session.scalar(select(User).where(User.tg_user_id == tg_user.id))
        if user is None:
            user = User(tg_user_id=tg_user.id, lang=tg_user.language_code or "ru")
            session.add(user)
            await session.commit()
            await session.refresh(user)

        data["user"] = user
        return await handler(event, data)


