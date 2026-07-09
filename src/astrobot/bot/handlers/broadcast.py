from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.handlers.menu import send_main_menu
from astrobot.bot.handlers.onboarding import prompt_for_name
from astrobot.bot.handlers.payment import _method_kb
from astrobot.bot.handlers.question import _answer_question
from astrobot.bot.keyboards import premium_or_back_kb, topics_kb
from astrobot.bot.platform.telegram import to_markup
from astrobot.bot.states import AskingQuestion
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, BroadcastVariant, User
from astrobot.limits import check_question, paywall_text
from astrobot.payments.catalog import get_item

router = Router(name="broadcast")


def _variant_question(variant: BroadcastVariant, idx: int) -> str | None:
    """Pull the preset question text from an `ask` button at position `idx`."""
    buttons = variant.buttons or []
    if idx < 0 or idx >= len(buttons):
        return None
    btn = buttons[idx]
    if not isinstance(btn, dict) or (btn.get("type") or "") != "ask":
        return None
    value = (btn.get("value") or "").strip()
    return value or None


@router.callback_query(F.data.startswith("bcast:ask:"))
async def on_broadcast_ask(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    """A broadcast 'Ask Astra' button: enter chat mode and immediately ask the
    preset question (consuming one question). Quota is enforced authoritatively
    inside _answer_question."""
    await call.answer()
    parts = call.data.split(":", 3)  # bcast:ask:{variant_id}:{idx}
    if len(parts) != 4:
        return
    try:
        variant_id, idx = int(parts[2]), int(parts[3])
    except ValueError:
        return

    variant = await session.get(BroadcastVariant, variant_id)
    if variant is None:
        return
    question = _variant_question(variant, idx)
    if not question:
        return

    profile = await need_profile(call.message, session, user)
    if profile is None:
        return

    allowance = await check_question(session, user)
    if not allowance.allowed:
        await call.message.answer(
            paywall_text("question", allowance), reply_markup=to_markup(premium_or_back_kb())
        )
        return

    await state.set_state(AskingQuestion.waiting_for_text)
    await call.message.answer(f"❓ <i>{question}</i>")
    await _answer_question(
        call.message,
        call.from_user.full_name if call.from_user else "User",
        session,
        user,
        profile,
        question,
    )


# The handlers below open existing flows as a NEW message (call.message.answer)
# rather than editing in place, so the broadcast itself is never replaced when a
# user taps one of its buttons.

@router.callback_query(F.data.startswith("bcast:buy:"))
async def on_broadcast_buy(call: CallbackQuery) -> None:
    await call.answer()
    code = call.data.split(":", 2)[2]  # bcast:buy:{code}
    item = get_item(code)
    if item is None:
        return
    await call.message.answer(
        f"<b>{item.title}</b> — {item.amount_rub} ₽\n\nВыбери способ оплаты:",
        reply_markup=to_markup(_method_kb(item)),
    )


@router.callback_query(F.data == "bcast:chat")
async def on_broadcast_chat(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    await call.answer()
    profile = await need_profile(call.message, session, user)
    if profile is None:
        return
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await call.message.answer(
            paywall_text("question", allowance), reply_markup=to_markup(premium_or_back_kb())
        )
        return
    await state.set_state(AskingQuestion.waiting_for_text)
    await call.message.answer(
        "🌙 Выбери тему — или напиши свой вопрос:", reply_markup=to_markup(topics_kb())
    )


@router.callback_query(F.data == "bcast:onb")
async def on_broadcast_onboarding(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    await call.answer()
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        # Already onboarded → just open the menu instead of restarting setup.
        await send_main_menu(call.message, user, session)
        return
    await prompt_for_name(call.message, state, user)
