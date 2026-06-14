from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import main_menu_inline
from astrobot.bot.responses import edit_or_send
from astrobot.db.models import User
from astrobot.limits import check_question, is_premium

router = Router(name="menu")


async def render_main_menu(user: User, session: AsyncSession) -> tuple[str, InlineKeyboardMarkup]:
    name = user.display_name or "путник"
    if is_premium(user):
        sub = (
            "💎 Премиум активен — звёзды без ограничений ✨\n"
            "🤝 Зови друзей: вам обоим +2 вопроса за каждого"
        )
    else:
        allow = await check_question(session, user)
        left = max(0, allow.limit - allow.used)
        sub = (
            f"✨ Бесплатных вопросов осталось: <b>{left}</b>\n"
            "💎 Премиум снимает лимиты \n 🤝 друг = +2 вопроса"
        )
    text = f"🔮 Привет, <b>{name}</b>! Выбери раздел:\n{sub}"
    return text, main_menu_inline()


async def send_main_menu(message: Message, user: User, session: AsyncSession) -> None:
    """Send the main menu as a single fresh inline message."""
    text, kb = await render_main_menu(user, session)
    await message.answer(text, reply_markup=kb)


@router.message(Command("menu"))
async def cmd_menu(message: Message, session: AsyncSession, user: User) -> None:
    await send_main_menu(message, user, session)


@router.callback_query(F.data == "menu:open")
async def on_menu_open(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    # Returning to the menu cancels any in-progress flow (asking a question,
    # push setup) so the next text isn't swallowed by a stale FSM state.
    await state.clear()
    text, kb = await render_main_menu(user, session)
    await edit_or_send(call, text, kb)
    await call.answer()
