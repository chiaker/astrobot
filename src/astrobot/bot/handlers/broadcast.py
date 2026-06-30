from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.handlers.question import _answer_question
from astrobot.bot.keyboards import premium_or_back_kb
from astrobot.bot.states import AskingQuestion
from astrobot.bot.utils import need_profile
from astrobot.db.models import BroadcastVariant, User
from astrobot.limits import check_question, paywall_text

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
            paywall_text("question", allowance), reply_markup=premium_or_back_kb()
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
