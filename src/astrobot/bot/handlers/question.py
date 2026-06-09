from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.bot.keyboards import MENU_QUESTION, cancel_kb
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.states import AskingQuestion
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, QuestionLog, User
from astrobot.llm.client import HistoryMessage, get_llm
from astrobot.llm.prompts import SYSTEM_QUESTION, split_brief_full

router = Router(name="question")


@router.message(F.text == MENU_QUESTION)
async def on_question_button(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    profile = await need_profile(message, session, user)
    if profile is None:
        return
    await state.set_state(AskingQuestion.waiting_for_text)
    await message.answer(
        "🌙 Слушаю.\n\n"
        "Спроси одним сообщением — я отвечу через твою карту.",
        reply_markup=cancel_kb(),
    )


@router.message(AskingQuestion.waiting_for_text)
async def on_question_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    question = (message.text or "").strip()
    if len(question) < 3:
        await message.answer("Слишком коротко — расскажи подробнее, что тебя волнует.")
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await state.clear()
        await message.answer("Сначала познакомимся — нажми /start.")
        return

    await state.clear()
    progress = await message.answer("🌟 Прикладываю карту к твоему вопросу…")

    birth = _profile_to_birth(profile, name=message.from_user.full_name or "User")
    chart = build_natal_chart(birth)
    natal_md = chart_to_markdown(chart)

    history_rows = await session.scalars(
        select(QuestionLog)
        .where(QuestionLog.user_id == user.id)
        .order_by(desc(QuestionLog.created_at))
        .limit(5)
    )
    history_pairs = list(history_rows)[::-1]
    history_msgs: list[HistoryMessage] = []
    for row in history_pairs:
        history_msgs.append(HistoryMessage(role="user", content=row.question))
        history_msgs.append(HistoryMessage(role="assistant", content=row.answer))

    llm = get_llm()
    response = await llm.complete(
        system=SYSTEM_QUESTION,
        cached_context=natal_md,
        user_message=question,
        history=history_msgs,
        max_tokens=2500,
        kind="question",
    )
    brief, full = split_brief_full(response.text)

    session.add(QuestionLog(user_id=user.id, question=question, answer=brief))
    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind="question",
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )

    await progress.delete()
    await save_and_send_response(message, session, user, "question", brief, full)
