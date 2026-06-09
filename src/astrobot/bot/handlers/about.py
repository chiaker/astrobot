from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from astrobot.bot.keyboards import MENU_ABOUT

router = Router(name="about")

ABOUT_TEXT = (
    "✨ <b>Об Астре</b>\n\n"
    "Меня зовут <b>Астра</b>. Я не предсказываю будущее — небо не книга судьбы, "
    "а зеркало. Я помогаю увидеть твои узоры через язык, на котором звёзды "
    "говорят уже тысячи лет.\n\n"
    "<b>Что я умею</b>\n"
    "🌟 Считаю твою <b>натальную карту</b> по дате, времени и месту рождения\n"
    "🔮 Смотрю текущие <b>транзиты</b> и рассказываю о периоде\n"
    "💬 Отвечаю на вопросы с учётом того, что записано в твоей карте\n\n"
    "<b>Команды</b>\n"
    "/start — пройти знакомство заново\n"
    "/cancel — отменить текущее действие\n\n"
    "<i>Карта подсказывает — а решаешь ты.</i>"
)


@router.message(F.text == MENU_ABOUT)
async def on_about(message: Message) -> None:
    await message.answer(ABOUT_TEXT, disable_web_page_preview=True)
