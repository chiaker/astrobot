from __future__ import annotations

import asyncio
from datetime import date, datetime, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
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
    with_back,
)
from astrobot.bot.responses import edit_or_send, save_and_send_response
from astrobot.bot.states import CompatFlow
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, Response, User
from astrobot.limits import check_question, consume_question_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_compatibility

router = Router(name="compatibility")

_KIND = "question:compatibility"


def _last_kb(resp_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💞 Новый расчёт", callback_data="compat:new")],
            [InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{resp_id}")],
            [MENU_BACK_BTN],
        ]
    )


async def _start_new(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    profile = await need_profile(call.message, session, user)
    if profile is None:
        return
    allowance = await check_question(session, user)
    if not allowance.allowed:
        await edit_or_send(call, paywall_text("question", allowance), premium_or_back_kb())
        return
    await state.set_state(CompatFlow.waiting_for_name)
    await edit_or_send(
        call,
        "💞 Проверим совместимость с другим человеком.\n\nКак его/её зовут?",
        with_back([]),
    )


@router.callback_query(F.data == "menu:compatibility")
async def on_compat_menu(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await call.answer()
    last = await session.scalar(
        select(Response)
        .where(Response.user_id == user.id, Response.kind == "compatibility")
        .order_by(desc(Response.created_at))
        .limit(1)
    )
    if last is not None:
        await edit_or_send(
            call,
            "💞 <i>Твой последний расчёт:</i>\n\n" + last.full,
            _last_kb(last.id),
        )
        return
    await _start_new(call, state, session, user)


@router.callback_query(F.data == "compat:new")
async def on_compat_new(
    call: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await call.answer()
    await _start_new(call, state, session, user)


@router.message(CompatFlow.waiting_for_name)
async def on_compat_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    name = "".join(ch for ch in name if ch not in "<>" and (ch == " " or ch.isprintable()))
    name = name.strip()
    if not (1 <= len(name) <= 64):
        await message.answer("Напиши имя (до 64 символов).")
        return
    await state.update_data(p_name=name)
    await state.set_state(CompatFlow.waiting_for_date)
    await message.answer(
        f"📅 Дата рождения <b>{name}</b> — в формате <code>DD.MM.YYYY</code>:"
    )


@router.message(CompatFlow.waiting_for_date)
async def on_compat_date(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        d = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Не понял дату. Нужно <code>DD.MM.YYYY</code>, например <code>14.03.1990</code>.")
        return
    if d.year < 1900 or d > date.today():
        await message.answer("Дата должна быть между 1900 годом и сегодняшним днём.")
        return
    await state.update_data(p_date=d.isoformat())
    await state.set_state(CompatFlow.waiting_for_time)
    await message.answer(
        "⏰ Время рождения <code>HH:MM</code> (или нажми «Не знаю времени»):",
        reply_markup=compat_time_unknown_kb(),
    )


@router.callback_query(CompatFlow.waiting_for_time, F.data == "compat:time:unknown")
async def on_compat_time_unknown(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(p_time=time(12, 0).isoformat(), p_time_unknown=True)
    await state.set_state(CompatFlow.waiting_for_city)
    await call.message.answer("📍 Город рождения этого человека:")
    await call.answer()


@router.message(CompatFlow.waiting_for_time)
async def on_compat_time(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        t = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await message.answer("Не понял время. Нужно <code>HH:MM</code>, или нажми «Не знаю времени».")
        return
    await state.update_data(p_time=t.isoformat(), p_time_unknown=False)
    await state.set_state(CompatFlow.waiting_for_city)
    await message.answer("📍 Город рождения этого человека:")


@router.message(CompatFlow.waiting_for_city)
async def on_compat_city(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Слишком короткое название. Введи город, например <code>Москва</code>.")
        return

    progress = await message.answer("🔍 Ищу город на карте…")
    result = await geocode_city(session, query)
    await progress.delete()
    if result is None:
        await message.answer(
            "Не нашла такой город — попробуй иначе, например <code>Санкт-Петербург, Россия</code>."
        )
        return

    data = await state.get_data()
    await state.clear()

    allowance = await check_question(session, user)
    if not allowance.allowed:
        await message.answer(
            paywall_text("question", allowance), reply_markup=premium_or_back_kb()
        )
        return

    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await message.answer("Сначала пройди /start — нужна твоя карта.")
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

    await _do_compat(message, session, user, me, partner, name_a, name_b)


async def _do_compat(
    target: Message,
    session: AsyncSession,
    user: User,
    me: BirthData,
    partner: BirthData,
    name_a: str,
    name_b: str,
) -> None:
    pre = await check_question(session, user)
    progress = await target.answer("💞 Сравниваю ваши карты…")

    report = await asyncio.to_thread(build_synastry_report, me, partner)
    context = synastry_to_markdown(report, name_a, name_b)

    llm = get_llm()
    response = await llm.complete(
        system=build_system_compatibility(user),
        cached_context=context,
        user_message=f"Дай разбор совместимости: {name_a} и {name_b}.",
        max_tokens=1800,
        kind=_KIND,
    )
    text = response.text

    consume_question_bonus_if_needed(user, pre.used)
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

    await progress.delete()
    header = f"💞 <b>{name_a} × {name_b}</b>\n\n"
    await save_and_send_response(target, session, user, "compatibility", header + text)
