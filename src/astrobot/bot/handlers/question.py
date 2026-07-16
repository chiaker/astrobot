from __future__ import annotations

import asyncio
import re

from aiogram import F, Router
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.bot.formatting import md_to_telegram_html
from astrobot.bot.handlers.menu import show_main_menu
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.bot.keyboards import (
    CHAT_EXIT_BTN,
    QUESTION_TOPICS,
    chat_answer_kb,
    premium_or_back_kb,
    topic_questions_kb,
    topics_kb,
)
from astrobot.bot.platform import Keyboard, PlatformContext
from astrobot.bot.responses import chunk_text
from astrobot.bot.states import AskingQuestion
from astrobot.bot.utils import need_profile_ctx, rate_limit_ok, user_llm_lock
from astrobot.db.models import BirthProfile, LLMUsageLog, QuestionLog, Response, User
from astrobot.limits import (
    check_question,
    consume_question_from_priority_bucket,
    paywall_text,
    reset_premium_questions_if_due,
)
from astrobot.llm.client import HistoryMessage, get_llm
from astrobot.llm.prompts import build_system_question
from astrobot.metrics import CRISIS_TRIGGERED
from astrobot.safety.crisis import CRISIS_REPLY, is_crisis

router = Router(name="question")

_REFUSAL_RE = re.compile(
    r"^(нет|не\s+хочу|не\s+надо|не\s+нужно|не\s+интересно"
    r"|не\s+сейчас|пока\s+нет|стоп|отмена|хватит|ничего)[,!.?…\s]*$",
    re.IGNORECASE,
)

_EXIT_KB = Keyboard.from_rows([[CHAT_EXIT_BTN]])

# Окно, в котором повторное нажатие того же вопроса считается дублем тапа, а не
# намерением спросить ещё раз. Ответ генерируется ~15–30 с, так что окно должно
# перекрывать генерацию с запасом на лаг доставки колбэка. Если MAX начнёт
# задерживать колбэки сильнее — поднять.
DUP_PRESS_WINDOW = 60


@router.callback_query(F.data == "menu:question")
async def on_question_button(
    ctx: PlatformContext, state, session: AsyncSession, user: User
) -> None:
    await ctx.answer_callback()
    profile = await need_profile_ctx(ctx, session, user)
    if profile is None:
        return
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
        return
    await state.set_state(AskingQuestion.waiting_for_text)
    await ctx.edit("🌙 Выбери тему — или напиши свой вопрос:", topics_kb())


async def _answer_question(
    ctx: PlatformContext,
    user_name: str,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
    question: str,
) -> None:
    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.reply("⏳ Секунду — предыдущий вопрос ещё обрабатывается.")
            return

        # Authoritative quota gate UNDER the lock.
        await session.refresh(user)
        reset_premium_questions_if_due(user)
        allowance = await check_question(session, user)
        if not allowance.allowed:
            await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
            return

        await ctx.reply("🌟 Прикладываю карту к твоему вопросу…")

        birth = _profile_to_birth(profile, name=user.display_name or user_name or "User")
        chart = await asyncio.to_thread(build_natal_chart, birth)
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
            system=build_system_question(user),
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
        resp_row = Response(user_id=user.id, kind="question", brief=response.text, full=response.text)
        session.add(resp_row)
        consume_question_from_priority_bucket(user)
        await session.flush()
        await session.commit()

        rendered = md_to_telegram_html(response.text)
        chunks = chunk_text(rendered)
        for i, chunk in enumerate(chunks):
            kb = chat_answer_kb(resp_row.id) if i == len(chunks) - 1 else None
            await ctx.reply(chunk, kb)

        # Пока вопросы ещё есть — премиум не упоминаем, просто зовём спросить
        # дальше. Пейволл только когда спрашивать больше нечем.
        after = await check_question(session, user)
        if not after.allowed:
            await ctx.reply(paywall_text("question", after), premium_or_back_kb())
            return
        await ctx.reply(
            "🌙 Что разберём дальше? Выбери тему — или просто напиши свой вопрос:",
            topics_kb(),
        )


@router.message(AskingQuestion.waiting_for_text)
async def on_question_text(
    ctx: PlatformContext, state, session: AsyncSession, user: User
) -> None:
    question = (ctx.text or "").strip()
    if len(question) < 3:
        await ctx.reply("Слишком коротко — расскажи подробнее, что тебя волнует.")
        return

    if _REFUSAL_RE.match(question):
        await ctx.reply("Хорошо! Напиши, когда захочешь что-то спросить.", _EXIT_KB)
        return

    if is_crisis(question):
        CRISIS_TRIGGERED.inc()
        await state.clear()
        await ctx.reply(CRISIS_REPLY, disable_preview=True)
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await state.clear()
        await ctx.reply("Сначала познакомимся — нажми /start.")
        return

    # Stay in chat mode (no state.clear) — the next message is a new question.
    await _answer_question(ctx, ctx.username or "User", session, user, profile, question)


@router.callback_query(F.data == "chat:exit")
async def on_chat_exit(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await state.clear()
    await ctx.answer_callback("Чат закрыт")
    await show_main_menu(ctx, user, session)


@router.callback_query(AskingQuestion.waiting_for_text, F.data == "chat:own_question")
async def on_own_question(ctx: PlatformContext) -> None:
    await ctx.answer_callback()
    await ctx.edit("✍️ Напиши свой вопрос:", _EXIT_KB)


@router.callback_query(AskingQuestion.waiting_for_text, F.data == "show_topics")
async def on_show_topics(ctx: PlatformContext) -> None:
    await ctx.answer_callback()
    await ctx.edit("🌙 Выбери тему — или просто спроси что-то своё одним сообщением:", topics_kb())


@router.callback_query(AskingQuestion.waiting_for_text, F.data.startswith("topic:"))
async def on_topic(ctx: PlatformContext) -> None:
    key = (ctx.payload or "").split(":", 1)[1]
    if key not in QUESTION_TOPICS:
        await ctx.answer_callback()
        return
    title = QUESTION_TOPICS[key][0]
    await ctx.answer_callback()
    await ctx.edit(f"<b>{title}</b>\n\nВыбери вопрос — или просто напиши свой:", topic_questions_kb(key))


@router.callback_query(AskingQuestion.waiting_for_text, F.data.startswith("q:"))
async def on_question_pick(
    ctx: PlatformContext, state, session: AsyncSession, user: User
) -> None:
    parts = (ctx.payload or "").split(":", 2)
    if len(parts) != 3:
        await ctx.answer_callback()
        return
    key, raw_idx = parts[1], parts[2]
    topic = QUESTION_TOPICS.get(key)
    try:
        question = topic[1][int(raw_idx)][1] if topic else None
    except (ValueError, IndexError):
        question = None
    if not question:
        await ctx.answer_callback()
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await state.clear()
        await ctx.answer_callback("Сначала пройди /start", alert=True)
        return

    allowance = await check_question(session, user)
    if not allowance.allowed:
        await state.clear()
        await ctx.answer_callback()
        await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
        return

    # Одно нажатие на конкретный вопрос за DUP_PRESS_WINDOW секунд. MAX иногда
    # лагает: человек жмёт кнопку второй раз, а второй колбэк доезжает уже ПОСЛЕ
    # первого ответа — user_llm_lock его не ловит (он про одновременность), и тот
    # же вопрос задаётся и списывается дважды. У соседних дорогих кнопок такой
    # дыры нет: натал и гороскоп на второе нажатие отдают кэш.
    # Проверка последней — чтобы отбитое пейволлом нажатие не съело окно.
    if not await rate_limit_ok(f"q:pick:{user.id}:{key}:{raw_idx}", 1, DUP_PRESS_WINDOW):
        await ctx.answer_callback("Уже отвечаю на этот вопрос ✨")
        return

    await ctx.answer_callback()
    # Stay in chat mode after a picked question.
    await ctx.reply(f"❓ <i>{question}</i>")
    await _answer_question(ctx, ctx.username or "User", session, user, profile, question)
