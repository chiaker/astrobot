from __future__ import annotations

import asyncio
from datetime import date, timedelta

from aiogram import F, Router
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
from astrobot.bot.platform import Button, PlatformContext
from astrobot.bot.responses import send_response
from astrobot.bot.utils import need_profile_ctx, user_llm_lock
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


def _regen_row(period: str) -> list[Button]:
    return [Button(text="🔄 Пересчитать заново", payload=f"horo:regen:{period}")]


@router.callback_query(F.data == "menu:horoscope")
async def on_horoscope_menu(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    profile = await need_profile_ctx(ctx, session, user)
    if profile is None:
        return
    await ctx.edit("🔮 На какой период посмотрим?", horoscope_period_kb(user))


@router.callback_query(F.data.startswith("horo:"))
async def on_horoscope_period(
    ctx: PlatformContext,
    session: AsyncSession,
    user: User,
) -> None:
    parts = (ctx.payload or "").split(":", 2)
    # horo:<period>  or  horo:regen:<period>
    if len(parts) == 3 and parts[1] == "regen":
        period: Period = parts[2]  # type: ignore[assignment]
        force_regen = True
    elif len(parts) == 2:
        period = parts[1]  # type: ignore[assignment]
        force_regen = False
    else:
        await ctx.answer_callback()
        return

    if period not in {"today", "week", "month"}:
        await ctx.answer_callback()
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await ctx.reply("Сначала пройди онбординг через /start.")
        await ctx.answer_callback()
        return

    display_name = user.display_name or "User"
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
            await ctx.answer_callback()
            await send_response(
                ctx,
                session,
                user,
                f"horoscope:{period}",
                _with_label(cached.full, label),
                extra_row=_regen_row(period),
            )
            return

    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.answer_callback("⏳ Уже считаю гороскоп — секунду…", alert=True)
            return

        # Rate limit under the lock (applies to both fresh and regen): re-read
        # committed usage so a burst can't each pass the check and waste LLM calls.
        await session.refresh(user)
        allowance = await check_horoscope(session, user)
        if not allowance.allowed:
            await ctx.answer_callback()
            await ctx.reply(paywall_text("horoscope", allowance), with_back([]))
            return

        await ctx.answer_callback()
        await ctx.reply("🔮 Смотрю, какие планеты идут к тебе сейчас…")

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

        await send_response(
            ctx,
            session,
            user,
            f"horoscope:{period}",
            _with_label(text, label),
            extra_row=_regen_row(period),
        )
