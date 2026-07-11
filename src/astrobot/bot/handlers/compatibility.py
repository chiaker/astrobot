from __future__ import annotations

import asyncio
from datetime import date, datetime, time

from aiogram import F, Router
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.geocoding import geocode_city
from astrobot.astrology.synastry import build_synastry_report, synastry_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.bot.keyboards import (
    MENU_BACK_BTN,
    compat_time_unknown_kb,
    premium_or_back_kb,
    promo_row,
    with_back,
)
from astrobot.bot.platform import Button, Keyboard, PlatformContext
from astrobot.bot.responses import send_response
from astrobot.bot.states import CompatFlow
from astrobot.bot.utils import need_profile_ctx, rate_limit_ok, user_llm_lock
from astrobot.db.models import BirthProfile, LLMUsageLog, Response, User
from astrobot.gender import guess_gender
from astrobot.limits import (
    check_question,
    consume_question_from_priority_bucket,
    paywall_text,
    reset_premium_questions_if_due,
)
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_compatibility

router = Router(name="compatibility")

GEOCODE_PER_HOUR = 20

_KIND = "question:compatibility"
_NEW_ROW = [Button(text="💞 Новый расчёт", payload="compat:new")]


def _last_kb(resp_id: int, user: User) -> Keyboard:
    rows: list[list[Button]] = [
        _NEW_ROW,
        [Button(text="⭐ Сохранить", payload=f"fav:save:{resp_id}")],
    ]
    if (pr := promo_row(user)):
        rows.append(pr)
    rows.append([MENU_BACK_BTN])
    return Keyboard.from_rows(rows)


def _gender_note(name_a: str, gender_a: str | None, name_b: str, gender_b: str | None) -> str:
    # Tell the LLM each person's gender so it agrees род correctly. Partner gender
    # is guessed from the name (ponytail: ambiguous names like «Саша» get no hint —
    # the model guesses, same as before; add an explicit ask-step if it matters).
    ru = {"m": "мужчина", "f": "женщина"}
    parts = [f"{n} — {ru[g]}" for n, g in ((name_a, gender_a), (name_b, gender_b)) if g in ru]
    if not parts:
        return ""
    return " Пол: " + ", ".join(parts) + ". Согласуй род прилагательных и глаголов по полу каждого."


async def _start_new(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    profile = await need_profile_ctx(ctx, session, user)
    if profile is None:
        return
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.edit(paywall_text("question", allowance), premium_or_back_kb())
        return
    await state.set_state(CompatFlow.waiting_for_name)
    await ctx.edit(
        "💞 Проверим совместимость с другим человеком.\n\nКак его/её зовут?",
        with_back([]),
    )


@router.callback_query(F.data == "menu:compatibility")
async def on_compat_menu(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    last = await session.scalar(
        select(Response)
        .where(Response.user_id == user.id, Response.kind == "compatibility")
        .order_by(desc(Response.created_at))
        .limit(1)
    )
    if last is not None:
        await ctx.edit(
            "💞 <i>Твой последний расчёт:</i>\n\n" + last.full,
            _last_kb(last.id, user),
        )
        return
    await _start_new(ctx, state, session, user)


@router.callback_query(F.data == "compat:new")
async def on_compat_new(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    await _start_new(ctx, state, session, user)


@router.message(CompatFlow.waiting_for_name)
async def on_compat_name(ctx: PlatformContext, state) -> None:
    name = (ctx.text or "").strip()
    name = "".join(ch for ch in name if ch not in "<>" and (ch == " " or ch.isprintable()))
    name = name.strip()
    if not (1 <= len(name) <= 64):
        await ctx.reply("Напиши имя (до 64 символов).")
        return
    await state.update_data(p_name=name)
    await state.set_state(CompatFlow.waiting_for_date)
    await ctx.reply(f"📅 Дата рождения <b>{name}</b> — в формате <code>DD.MM.YYYY</code>:")


@router.message(CompatFlow.waiting_for_date)
async def on_compat_date(ctx: PlatformContext, state) -> None:
    text = (ctx.text or "").strip()
    try:
        d = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await ctx.reply("Не понял дату. Нужно <code>DD.MM.YYYY</code>, например <code>14.03.1990</code>.")
        return
    if d.year < 1900 or d > date.today():
        await ctx.reply("Дата должна быть между 1900 годом и сегодняшним днём.")
        return
    await state.update_data(p_date=d.isoformat())
    await state.set_state(CompatFlow.waiting_for_time)
    await ctx.reply(
        "⏰ Время рождения <code>HH:MM</code> (или нажми «Не знаю времени»):",
        compat_time_unknown_kb(),
    )


@router.callback_query(CompatFlow.waiting_for_time, F.data == "compat:time:unknown")
async def on_compat_time_unknown(ctx: PlatformContext, state) -> None:
    await state.update_data(p_time=time(12, 0).isoformat(), p_time_unknown=True)
    await state.set_state(CompatFlow.waiting_for_city)
    await ctx.reply("📍 Город рождения этого человека:")
    await ctx.answer_callback()


@router.message(CompatFlow.waiting_for_time)
async def on_compat_time(ctx: PlatformContext, state) -> None:
    text = (ctx.text or "").strip()
    try:
        t = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await ctx.reply("Не понял время. Нужно <code>HH:MM</code>, или нажми «Не знаю времени».")
        return
    await state.update_data(p_time=t.isoformat(), p_time_unknown=False)
    await state.set_state(CompatFlow.waiting_for_city)
    await ctx.reply("📍 Город рождения этого человека:")


@router.message(CompatFlow.waiting_for_city)
async def on_compat_city(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    query = (ctx.text or "").strip()
    if len(query) < 2:
        await ctx.reply("Слишком короткое название. Введи город, например <code>Москва</code>.")
        return

    if not await rate_limit_ok(f"geo:rl:{user.id}", GEOCODE_PER_HOUR, 3600):
        await ctx.reply("⏳ Слишком много запросов городов подряд. Попробуй через несколько минут.")
        return

    await ctx.reply("🔍 Ищу город на карте…")
    result = await geocode_city(session, query)
    if result is None:
        await ctx.reply(
            "Не нашла такой город — попробуй иначе, например <code>Санкт-Петербург, Россия</code>."
        )
        return

    data = await state.get_data()
    await state.clear()

    allowance = await check_question(session, user)
    if not allowance.allowed:
        await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await ctx.reply("Сначала пройди /start — нужна твоя карта.")
        return

    partner = BirthData(
        name=data["p_name"],
        date=date.fromisoformat(data["p_date"]),
        time=time.fromisoformat(data["p_time"]),
        time_unknown=data.get("p_time_unknown", False),
        lat=result.lat,
        lon=result.lon,
        tz=result.tz,
        city_name=result.display_name,
    )
    name_a = user.display_name or "Ты"
    name_b = data["p_name"]
    if name_a == name_b:
        name_a = f"{name_a} (ты)"
    me = _profile_to_birth(profile, name=name_a)

    await _do_compat(ctx, session, user, me, partner, name_a, name_b)


async def _do_compat(
    ctx: PlatformContext,
    session: AsyncSession,
    user: User,
    me: BirthData,
    partner: BirthData,
    name_a: str,
    name_b: str,
) -> None:
    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.reply("⏳ Секунду — предыдущий расчёт ещё считается.")
            return

        await session.refresh(user)
        reset_premium_questions_if_due(user)
        allowance = await check_question(session, user)
        if not allowance.allowed:
            await ctx.reply(paywall_text("question", allowance), premium_or_back_kb())
            return

        await ctx.reply("💞 Сравниваю ваши карты…")

        report = await asyncio.to_thread(build_synastry_report, me, partner)
        context = synastry_to_markdown(report, name_a, name_b)

        llm = get_llm()
        response = await llm.complete(
            system=build_system_compatibility(user),
            cached_context=context,
            user_message=(
                f"Дай разбор совместимости: {name_a} и {name_b}."
                + _gender_note(name_a, user.gender, name_b, guess_gender(name_b))
            ),
            max_tokens=2800,
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

        header = f"💞 <b>{name_a} × {name_b}</b>\n\n"
        await send_response(ctx, session, user, "compatibility", header + text, extra_row=_NEW_ROW)
