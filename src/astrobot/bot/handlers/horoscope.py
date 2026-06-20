from __future__ import annotations

import asyncio
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
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
from astrobot.bot.keyboards import horoscope_period_kb, with_back
from astrobot.bot.responses import edit_or_send, save_and_send_response
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, HoroscopeCache, LLMUsageLog, User
from astrobot.limits import check_horoscope, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_horoscope

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


def _regen_row(period: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🔄 Пересчитать заново", callback_data=f"horo:regen:{period}")]


@router.callback_query(F.data == "menu:horoscope")
async def on_horoscope_menu(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    profile = await need_profile(call.message, session, user)
    if profile is None:
        return
    await edit_or_send(call, "🔮 На какой период посмотрим?", horoscope_period_kb(user))


@router.callback_query(F.data.startswith("horo:"))
async def on_horoscope_period(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    parts = call.data.split(":", 2)
    # horo:<period>  or  horo:regen:<period>
    if len(parts) == 3 and parts[1] == "regen":
        period: Period = parts[2]  # type: ignore[assignment]
        force_regen = True
    elif len(parts) == 2:
        period = parts[1]  # type: ignore[assignment]
        force_regen = False
    else:
        await call.answer()
        return

    if period not in {"today", "week", "month"}:
        await call.answer()
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.message.answer("Сначала пройди онбординг через /start.")
        await call.answer()
        return

    display_name = user.display_name or call.from_user.full_name or "User"
    birth = _profile_to_birth(profile, name=display_name)
    today = midnight_today_in(birth.tz)
    label = _period_label(period, today)

    # Use cached version unless forced regen
    if not force_regen:
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
                _with_label(cached.full, label),
                extra_row=_regen_row(period),
            )
            return

    # Check rate limit (applies to both fresh and regen)
    allowance = await check_horoscope(session, user)
    if not allowance.allowed:
        await call.answer()
        await call.message.answer(
            paywall_text("horoscope", allowance), reply_markup=with_back([])
        )
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
        system=build_system_horoscope(user),
        cached_context=cached_context,
        user_message=user_prompt,
        max_tokens=2800,
        kind=f"horoscope_{period}",
    )
    text = response.text

    # Update or create cache entry
    existing = await session.scalar(
        select(HoroscopeCache).where(
            HoroscopeCache.user_id == user.id,
            HoroscopeCache.period == period,
        )
    )
    if existing:
        existing.computed_for = today
        existing.brief = text
        existing.full = text
    else:
        session.add(
            HoroscopeCache(
                user_id=user.id,
                period=period,
                computed_for=today,
                brief=text,
                full=text,
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
        _with_label(text, label),
        extra_row=_regen_row(period),
    )
