from __future__ import annotations

from aiogram import F, Router

from astrobot.bot.keyboards import MENU_BACK_BTN, with_back
from astrobot.bot.platform import Keyboard, PlatformContext
from astrobot.config import get_settings
from astrobot.db.models import User
from astrobot.legal.disclaimer import SHORT_DISCLAIMER
from astrobot.referral import build_share_link

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


def _about_kb() -> Keyboard:
    return Keyboard.from_rows([[MENU_BACK_BTN]])


# Reference handler migrated to the platform layer (Этап 2): no aiogram types,
# no to_markup — `ctx` speaks the neutral interface, works on Telegram and MAX.
@router.callback_query(F.data == "menu:about")
async def on_about(ctx: PlatformContext) -> None:
    await ctx.answer_callback()
    await ctx.edit(ABOUT_TEXT, _about_kb())


@router.callback_query(F.data == "referral:show")
async def on_referral_show(ctx: PlatformContext, user: User) -> None:
    s = get_settings()
    link = build_share_link(s.bot_username or "your_bot", user.referral_code, s.platform)
    text = (
        "🤝 <b>Пригласи друга к Астре</b>\n\n"
        f"Твоя реферальная ссылка:\n<code>{link}</code>\n\n"
        "За каждого друга, который зарегистрируется по ней, "
        "<b>вы оба получите +2 бесплатных вопроса</b> ✨\n\n"
        "Просто поделись ссылкой в любом мессенджере."
    )
    await ctx.edit(text, with_back([]))
    await ctx.answer_callback()
