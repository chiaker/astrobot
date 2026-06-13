from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.handlers.menu import send_main_menu
from astrobot.db.models import User

router = Router(name="fallback")


@router.message()
async def fallback(message: Message, session: AsyncSession, user: User) -> None:
    await message.answer(
        "🌙 Я слушаю через меню — выбери раздел ниже или открой /menu."
    )
    await send_main_menu(message, user, session)
