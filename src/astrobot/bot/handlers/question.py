from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.bot.formatting import md_to_telegram_html
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.bot.keyboards import (
    MENU_QUESTION,
    SUGGESTED_QUESTIONS,
    ask_again_with_save_kb,
    question_entry_kb,
    suggested_questions_kb,
)
from astrobot.bot.responses import chunk_text, safe_answer
from astrobot.bot.states import AskingQuestion
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, QuestionLog, Response, User
from astrobot.limits import (
    check_question,
    consume_question_bonus_if_needed,
    is_premium,
    paywall_text,
)
from astrobot.llm.client import HistoryMessage, get_llm
from astrobot.llm.prompts import SYSTEM_QUESTION
from astrobot.metrics import CRISIS_TRIGGERED
from astrobot.safety.crisis import CRISIS_REPLY, is_crisis

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
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await message.answer(paywall_text("question", allowance))
        return
    await state.set_state(AskingQuestion.waiting_for_text)
    await message.answer(
        "🌙 Слушаю.\n\nСпроси меня одним сообщением — я отвечу через твою карту.",
        reply_markup=question_entry_kb(),
    )


async def _answer_question(
    target: Message,
    user_name: str,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
    question: str,
) -> None:
    progress = await target.answer("🌟 Прикладываю карту к твоему вопросу…")

    # Pre-call snapshot for bonus accounting
    pre_call_allowance = await check_question(session, user)
    pre_call_used = pre_call_allowance.used

    birth = _profile_to_birth(profile, name=user_name or "User")
    chart = build_natal_chart(birth)
    natal_md = chart_to_markdown(chart)

    history_rows = await session.scalars(
        select(QuestionLog)
        .where(QuestionLog.user_id == user.id)
        .order_by(desc(QuestionLog.created_at))
        .limit(5)
    )
    history_msgs: list[HistoryMessage] = []
    for row in list(history_rows)[::-1]:
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

    session.add(QuestionLog(user_id=user.id, question=question, answer=response.text))
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
    resp_row = Response(
        user_id=user.id,
        kind="question",
        brief=response.text,
        full=response.text,
    )
    session.add(resp_row)
    consume_question_bonus_if_needed(user, pre_call_used)
    await session.flush()
    await session.commit()

    await progress.delete()
    rendered = md_to_telegram_html(response.text)
    chunks = chunk_text(rendered)
    for i, chunk in enumerate(chunks):
        kb = ask_again_with_save_kb(resp_row.id) if i == len(chunks) - 1 else None
        await safe_answer(target, chunk, reply_markup=kb)

    # Soft-upsell for free users near their limit
    if not is_premium(user):
        q_left_check = await check_question(session, user)
        left = max(0, q_left_check.limit - q_left_check.used)
        if left == 0:
            await target.answer(
                "🌙 Это был твой последний бесплатный вопрос. "
                "Если хочешь продолжать — открой <b>💎 Премиум</b>."
            )
        elif left == 1:
            await target.answer(
                "🌙 У тебя остался <b>1 вопрос</b> на бесплатном тарифе. "
                "Премиум снимает границы ✨"
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

    if is_crisis(question):
        CRISIS_TRIGGERED.inc()
        await state.clear()
        await message.answer(CRISIS_REPLY, disable_web_page_preview=True)
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await state.clear()
        await message.answer("Сначала познакомимся — нажми /start.")
        return

    await state.clear()
    await _answer_question(
        message,
        message.from_user.full_name if message.from_user else "User",
        session,
        user,
        profile,
        question,
    )


@router.callback_query(F.data == "ask_again")
async def on_ask_again(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.answer("Сначала пройди /start", show_alert=True)
        return

    allowance = await check_question(session, user)
    if not allowance.allowed:
        await call.answer()
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await call.message.answer(paywall_text("question", allowance))
        return

    await call.answer()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await state.set_state(AskingQuestion.waiting_for_text)
    await call.message.answer(
        "🌙 Слушаю.\n\nСпроси меня одним сообщением — я отвечу через твою карту.",
        reply_markup=question_entry_kb(),
    )


@router.callback_query(AskingQuestion.waiting_for_text, F.data == "show_topics")
async def on_show_topics(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text(
            "🌙 Выбери тему — или просто спроси что-то своё одним сообщением:",
            reply_markup=suggested_questions_kb(),
        )
    except Exception:
        pass
    await call.answer()


@router.callback_query(AskingQuestion.waiting_for_text, F.data.startswith("ask:"))
async def on_suggested(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    key = call.data.split(":", 1)[1]
    question = SUGGESTED_QUESTIONS.get(key)
    if not question:
        await call.answer()
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await state.clear()
        await call.answer("Сначала пройди /start", show_alert=True)
        return

    await call.answer()
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"❓ <i>{question}</i>")
    await _answer_question(
        call.message,
        call.from_user.full_name if call.from_user else "User",
        session,
        user,
        profile,
        question,
    )
