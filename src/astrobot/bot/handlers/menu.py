from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import main_menu_inline
from astrobot.bot.responses import edit_or_send
from astrobot.db.models import User
from astrobot.limits import check_question, is_premium

router = Router(name="menu")


async def render_main_menu(user: User, session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    name = user.display_name or "путник"
    if is_premium(user):
        sub = "💎 Премиум активен — звёзды без ограничений ✨"
    else:
        allow = await check_question(session, user)
        left = max(0, allow.limit - allow.used)
        sub = f"✨ Бесплатных вопросов осталось: <b>{left}</b>"
    text = f"🔮 Привет, <b>{name}</b>! Выбери раздел:\n{sub}"
    return text, main_menu_inline()


async def send_main_menu(message: Message, user: User, session: AsyncSession) -> None:
    """Send the main menu as a fresh message and clear any stale reply keyboard
    left over from the old UI (one bubble via edit_reply_markup)."""
    text, kb = await render_main_menu(user, session)
    sent = await message.answer(text, reply_markup=ReplyKeyboardRemove())
    try:
        await sent.edit_reply_markup(reply_markup=kb)
    except Exception:
        # Fallback: separate message with the inline menu.
        await message.answer(text, reply_markup=kb)


@router.message(Command("menu"))
async def cmd_menu(message: Message, session: AsyncSession, user: User) -> None:
    await send_main_menu(message, user, session)


@router.callback_query(F.data == "menu:open")
async def on_menu_open(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    text, kb = await render_main_menu(user, session)
    await edit_or_send(call, text, kb)
    await call.answer()
