from __future__ import annotations

from aiogram import F, Router
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_BACK_BTN, premium_or_back_kb, promo_row, tarot_entry_kb
from astrobot.bot.platform import Button, Keyboard, PlatformContext
from astrobot.bot.responses import send_response
from astrobot.bot.states import TarotFlow
from astrobot.bot.utils import user_llm_lock
from astrobot.db.models import LLMUsageLog, Response, User
from astrobot.limits import (
    check_question,
    consume_question_from_priority_bucket,
    paywall_text,
    reset_premium_questions_if_due,
)
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_tarot
from astrobot.metrics import CRISIS_TRIGGERED
from astrobot.safety.crisis import CRISIS_REPLY, is_crisis
from astrobot.tarot import cards_to_markdown, draw_three

router = Router(name="tarot")

_KIND = "question:tarot"
_NEW_ROW = [Button(text="🃏 Новый расклад", payload="tarot:new")]


def _last_kb(resp_id: int, user: User) -> Keyboard:
    rows: list[list[Button]] = [
        _NEW_ROW,
        [Button(text="⭐ Сохранить", payload=f"fav:save:{resp_id}")],
    ]
    if (pr := promo_row(user)):
        rows.append(pr)
    rows.append([MENU_BACK_BTN])
    return Keyboard.from_rows(rows)


async def _start_new(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.edit(paywall_text("question", allowance), premium_or_back_kb())
        return
    await state.set_state(TarotFlow.waiting_for_question)
    await ctx.edit(
        "🃏 О чём спросить карты? Напиши вопрос одним сообщением — или тяни без вопроса.",
        tarot_entry_kb(),
    )


@router.callback_query(F.data == "menu:tarot")
async def on_tarot_menu(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    last = await session.scalar(
        select(Response)
        .where(Response.user_id == user.id, Response.kind == "tarot")
        .order_by(desc(Response.created_at))
        .limit(1)
    )
    if last is not None:
        await ctx.edit(
            "🃏 <i>Твой последний расклад:</i>\n\n" + last.full,
            _last_kb(last.id, user),
        )
        return
    await _start_new(ctx, state, session, user)


@router.callback_query(F.data == "tarot:new")
async def on_tarot_new(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    await _start_new(ctx, state, session, user)


@router.callback_query(F.data == "tarot:draw")
async def on_tarot_draw(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    await state.clear()
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
        return
    await _do_tarot(ctx, session, user, question=None)


@router.message(TarotFlow.waiting_for_question)
async def on_tarot_question(
    ctx: PlatformContext, state, session: AsyncSession, user: User
) -> None:
    question = (ctx.text or "").strip()
    if is_crisis(question):
        CRISIS_TRIGGERED.inc()
        await state.clear()
        await ctx.reply(CRISIS_REPLY, disable_preview=True)
        return
    await state.clear()
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
        return
    await _do_tarot(ctx, session, user, question=question[:500] or None)


async def _do_tarot(
    ctx: PlatformContext, session: AsyncSession, user: User, question: str | None
) -> None:
    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.reply("⏳ Секунду — предыдущий расклад ещё раскрывается.")
            return

        # Authoritative quota gate under the lock.
        await session.refresh(user)
        reset_premium_questions_if_due(user)
        allowance = await check_question(session, user)
        if not allowance.allowed:
            await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
            return

        await ctx.reply("🃏 Тасую колоду и раскладываю карты…")

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
        text = response.text

        consume_question_from_priority_bucket(user)
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

        header = f"🃏 <b>Расклад:</b> {spread}\n\n"
        await send_response(ctx, session, user, "tarot", header + text, extra_row=_NEW_ROW)
