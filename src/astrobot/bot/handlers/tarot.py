from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_BACK_BTN, premium_or_back_kb, tarot_entry_kb
from astrobot.bot.responses import edit_or_send, save_and_send_response
from astrobot.bot.states import TarotFlow
from astrobot.db.models import LLMUsageLog, Response, User
from astrobot.limits import check_question, consume_question_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_tarot, split_brief_full
from astrobot.metrics import CRISIS_TRIGGERED
from astrobot.safety.crisis import CRISIS_REPLY, is_crisis
from astrobot.tarot import cards_to_markdown, draw_three

router = Router(name="tarot")

_KIND = "question:tarot"


def _last_kb(resp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🃏 Новый расклад", callback_data="tarot:new")],
            [InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{resp_id}")],
            [MENU_BACK_BTN],
        ]
    )


async def _start_new(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await edit_or_send(call, paywall_text("question", allowance), premium_or_back_kb())
        return
    await state.set_state(TarotFlow.waiting_for_question)
    await edit_or_send(
        call,
        "🃏 О чём спросить карты? Напиши вопрос одним сообщением — или тяни без вопроса.",
        tarot_entry_kb(),
    )


@router.callback_query(F.data == "menu:tarot")
async def on_tarot_menu(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await call.answer()
    last = await session.scalar(
        select(Response)
        .where(Response.user_id == user.id, Response.kind == "tarot")
        .order_by(desc(Response.created_at))
        .limit(1)
    )
    if last is not None:
        await edit_or_send(
            call,
            "🃏 <i>Твой последний расклад:</i>\n\n" + last.full,
            _last_kb(last.id),
        )
        return
    await _start_new(call, state, session, user)


@router.callback_query(F.data == "tarot:new")
async def on_tarot_new(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await call.answer()
    await _start_new(call, state, session, user)


@router.callback_query(F.data == "tarot:draw")
async def on_tarot_draw(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await call.answer()
    await state.clear()
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await call.message.answer(
            paywall_text("question", allowance), reply_markup=premium_or_back_kb()
        )
        return
    await _do_tarot(call.message, session, user, question=None)


@router.message(TarotFlow.waiting_for_question)
async def on_tarot_question(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    question = (message.text or "").strip()
    if is_crisis(question):
        CRISIS_TRIGGERED.inc()
        await state.clear()
        await message.answer(CRISIS_REPLY, disable_web_page_preview=True)
        return
    await state.clear()
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await message.answer(
            paywall_text("question", allowance), reply_markup=premium_or_back_kb()
        )
        return
    await _do_tarot(message, session, user, question=question[:500] or None)


async def _do_tarot(
    target: Message, session: AsyncSession, user: User, question: str | None
) -> None:
    pre = await check_question(session, user)
    progress = await target.answer("🃏 Тасую колоду и раскладываю карты…")

    cards = draw_three()
    context = cards_to_markdown(cards, question)
    spread = " · ".join(
        f"{c.position}: {c.name}{' (перевёрн.)' if c.reversed else ''}" for c in cards
    )

    llm = get_llm()
    response = await llm.complete(
        system=build_system_tarot(user),
        cached_context=context,
        user_message=question or "Сделай общий расклад на ближайшее время.",
        max_tokens=1500,
        kind=_KIND,
    )
    brief, full = split_brief_full(response.text)

    consume_question_bonus_if_needed(user, pre.used)
    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind=_KIND,
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )

    await progress.delete()
    header = f"🃏 <b>Расклад:</b> {spread}\n\n"
    await save_and_send_response(
        target, session, user, "tarot", header + brief, header + full
    )
