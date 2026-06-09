from __future__ import annotations

import asyncio
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
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
from astrobot.db.models import BirthProfile, HoroscopeCache, LLMUsageLog, User
from astrobot.limits import check_horoscope, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import SYSTEM_HOROSCOPE, split_brief_full

router = Router(name="horoscope")


def _period_label(period: Period, today: date) -> str:
    if period == "today":
        return f"📅 <i>Гороскоп на {today.strftime('%d.%m.%Y')}</i>"
    if period == "week":
        end = today + timedelta(days=6)
        return (
            f"📅 <i>Гороскоп на неделю: {today.strftime('%d.%m')} — "
            f"{end.strftime('%d.%m.%Y')}</i>"
        )
    end = today + timedelta(days=29)
    return (
        f"📅 <i>Гороскоп на месяц: {today.strftime('%d.%m')} — "
        f"{end.strftime('%d.%m.%Y')}</i>"
    )


def _with_label(text: str, label: str) -> str:
    return f"{label}\n\n{text}"


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

    birth = _profile_to_birth(profile, name=call.from_user.full_name or "User")
    today = midnight_today_in(birth.tz)
    label = _period_label(period, today)

    cached = await session.scalar(
        select(HoroscopeCache).where(
            HoroscopeCache.user_id == user.id,
            HoroscopeCache.period == period,
        )
    )
    if cached and cached.computed_for == today:
        await call.answer()
        await save_and_send_response(
            call.message,
            session,
            user,
            f"horoscope:{period}",
            _with_label(cached.brief, label),
            _with_label(cached.full, label),
        )
        return

    allowance = await check_horoscope(session, user)
    if not allowance.allowed:
        await call.answer()
        await call.message.answer(paywall_text("horoscope", allowance))
        return

    await call.answer()
    progress = await call.message.answer("🔮 Смотрю, какие планеты идут к тебе сейчас…")

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

    if cached:
        cached.computed_for = today
        cached.brief = brief
        cached.full = full
    else:
        session.add(
            HoroscopeCache(
                user_id=user.id,
                period=period,
                computed_for=today,
                brief=brief,
                full=full,
            )
        )

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
        call.message,
        session,
        user,
        f"horoscope:{period}",
        _with_label(brief, label),
        _with_label(full, label),
    )
