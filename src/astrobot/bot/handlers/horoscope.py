from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.transits import (
    Period,
    build_transit_report,
    midnight_today_in,
    transit_report_to_markdown,
)
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.bot.keyboards import (
    MENU_HOROSCOPE,
    horoscope_period_kb,
)
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import SYSTEM_HOROSCOPE, split_brief_full

router = Router(name="horoscope")


@router.message(F.text == MENU_HOROSCOPE)
async def on_horoscope_menu(message: Message, session: AsyncSession, user: User) -> None:
    profile = await need_profile(message, session, user)
    if profile is None:
        return
    await message.answer(
        "🔮 На какой период посмотрим?", reply_markup=horoscope_period_kb()
    )


@router.callback_query(F.data.startswith("horo:"))
async def on_horoscope_period(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    period: Period = call.data.split(":", 1)[1]  # type: ignore[assignment]
    if period not in {"today", "week", "month"}:
        await call.answer()
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.message.answer("Сначала пройди онбординг через /start.")
        await call.answer()
        return

    await call.answer()
    progress = await call.message.answer("🔮 Смотрю, какие планеты идут к тебе сейчас…")

    birth = _profile_to_birth(profile, name=call.from_user.full_name or "User")
    today = midnight_today_in(birth.tz)

    chart = await asyncio.to_thread(build_natal_chart, birth)
    natal_md = chart_to_markdown(chart)

    report = await asyncio.to_thread(build_transit_report, birth, today, period)
    transits_md = transit_report_to_markdown(report)

    cached_context = natal_md + "\n\n" + transits_md
    user_prompt = {
        "today": "Дай гороскоп на сегодня.",
        "week": "Дай гороскоп на ближайшую неделю.",
        "month": "Дай гороскоп на ближайший месяц.",
    }[period]

    await progress.edit_text("✨ Складываю узор периода…")

    llm = get_llm()
    response = await llm.complete(
        system=SYSTEM_HOROSCOPE,
        cached_context=cached_context,
        user_message=user_prompt,
        max_tokens=2800,
        kind=f"horoscope_{period}",
    )
    brief, full = split_brief_full(response.text)

    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind=f"horoscope:{period}",
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )

    await progress.delete()
    await save_and_send_response(
        call.message, session, user, f"horoscope:{period}", brief, full
    )
