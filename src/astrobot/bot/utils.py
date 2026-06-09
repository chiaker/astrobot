from __future__ import annotations

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import BirthProfile, User


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
