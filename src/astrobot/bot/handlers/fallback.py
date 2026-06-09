from aiogram import Router
from aiogram.types import Message

from astrobot.bot.keyboards import main_menu

router = Router(name="fallback")


@router.message()
async def fallback(message: Message) -> None:
    await message.answer(
        "🌙 Я слушаю только через меню — выбери что тебя сейчас интересует. "
        "Если меню пропало — нажми /start.",
        reply_markup=main_menu(),
    )
