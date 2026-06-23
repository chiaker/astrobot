from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from astrobot.bot.keyboards import MENU_BACK_BTN, with_back
from astrobot.bot.responses import edit_or_send
from astrobot.config import get_settings
from astrobot.db.models import User
from astrobot.legal.disclaimer import SHORT_DISCLAIMER

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
    "<i>Карта подсказывает — а решаешь ты.</i>\n\n"
    + SHORT_DISCLAIMER
)


def _about_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="💎 Премиум", callback_data="menu:premium")],
        [MENU_BACK_BTN],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:about")
async def on_about(call: CallbackQuery) -> None:
    await call.answer()
    await edit_or_send(call, ABOUT_TEXT, _about_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "referral:show")
async def on_referral_show(call: CallbackQuery, user: User) -> None:
    bot_username = get_settings().bot_username or "your_bot"
    link = f"https://t.me/{bot_username}?start=ref_{user.referral_code}"
    text = (
        "🤝 <b>Пригласи друга к Астре</b>\n\n"
        f"Твоя реферальная ссылка:\n<code>{link}</code>\n\n"
        "За каждого друга, который зарегистрируется по ней, "
        "<b>вы оба получите +2 бесплатных вопроса</b> ✨\n\n"
        "Просто поделись ссылкой в любом мессенджере."
    )
    await edit_or_send(call, text, with_back([]), disable_web_page_preview=True)
    await call.answer()
