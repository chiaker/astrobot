from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from astrobot.bot.keyboards import MENU_ABOUT

router = Router(name="about")

ABOUT_TEXT = (
    "<b>ℹ️ О боте</b>\n\n"
    "Я считаю твою <b>натальную карту</b> по дате, времени и месту рождения, "
    "разбираю текущие <b>транзиты</b> и отвечаю на вопросы с учётом твоей карты.\n\n"
    "<b>Команды</b>\n"
    "/start — заново пройти онбординг\n"
    "/cancel — отменить текущее действие\n\n"
)


@router.message(F.text == MENU_ABOUT)
async def on_about(message: Message) -> None:
    await message.answer(ABOUT_TEXT, disable_web_page_preview=True)
