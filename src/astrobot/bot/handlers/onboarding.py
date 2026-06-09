from __future__ import annotations

from datetime import date, datetime, time

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.geocoding import geocode_city
from astrobot.bot.keyboards import (
    confirm_kb,
    main_menu,
    time_unknown_kb,
)
from astrobot.bot.states import Onboarding
from astrobot.db.models import BirthProfile, User

router = Router(name="onboarding")


def _format_summary(data: dict) -> str:
    d = date.fromisoformat(data["birth_date"])
    t = time.fromisoformat(data["birth_time"])
    time_str = "неизвестно (солнечная карта)" if data["time_unknown"] else t.strftime("%H:%M")
    return (
        "<b>Проверь данные:</b>\n"
        f"📅 Дата: {d.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {time_str}\n"
        f"📍 Место: {data['city_display']}\n"
        f"🌐 Часовой пояс: {data['tz']}"
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    await state.clear()
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await message.answer(
            "С возвращением! Выбери, что тебя интересует:",
            reply_markup=main_menu(),
        )
        return

    await message.answer(
        "Привет! Я бот-астролог. Для построения натальной карты мне нужны "
        "дата, время и место твоего рождения.\n\n"
        "Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code> (например, 14.03.1990):"
    )
    await state.set_state(Onboarding.waiting_for_date)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())


@router.message(Onboarding.waiting_for_date)
async def on_date(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        birth_date = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Не понял дату. Нужно <code>DD.MM.YYYY</code>, например <code>14.03.1990</code>.")
        return

    if birth_date.year < 1900 or birth_date > date.today():
        await message.answer("Дата должна быть между 1900 годом и сегодняшним днём.")
        return

    await state.update_data(birth_date=birth_date.isoformat())
    await message.answer(
        "Теперь введи <b>время рождения</b> в формате <code>HH:MM</code> "
        "(например, <code>14:30</code>).\n\n"
        "Если точное время неизвестно — нажми кнопку ниже, я построю солнечную карту "
        "(без домов и Асцендента).",
        reply_markup=time_unknown_kb(),
    )
    await state.set_state(Onboarding.waiting_for_time)


@router.callback_query(Onboarding.waiting_for_time, F.data == "time:unknown")
async def on_time_unknown(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(birth_time=time(12, 0).isoformat(), time_unknown=True)
    await call.message.answer(
        "Хорошо, строим солнечную карту. Теперь введи <b>город рождения</b> "
        "(например, <code>Москва</code> или <code>Новосибирск</code>):"
    )
    await state.set_state(Onboarding.waiting_for_city)
    await call.answer()


@router.message(Onboarding.waiting_for_time)
async def on_time(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        birth_time = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await message.answer(
            "Не понял время. Нужно <code>HH:MM</code>, например <code>14:30</code>. "
            "Или нажми кнопку «Не знаю точного времени»."
        )
        return

    await state.update_data(birth_time=birth_time.isoformat(), time_unknown=False)
    await message.answer(
        "Отлично. Теперь введи <b>город рождения</b> "
        "(например, <code>Москва</code> или <code>Новосибирск</code>):"
    )
    await state.set_state(Onboarding.waiting_for_city)


@router.message(Onboarding.waiting_for_city)
async def on_city(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Слишком короткое название. Введи город, например <code>Москва</code>.")
        return

    progress = await message.answer("Ищу город…")
    result = await geocode_city(session, query)
    await progress.delete()

    if result is None:
        await message.answer(
            "Не нашёл такой город. Попробуй ввести по-другому "
            "(например, <code>Санкт-Петербург, Россия</code>)."
        )
        return

    await state.update_data(
        lat=result.lat,
        lon=result.lon,
        tz=result.tz,
        city_display=result.display_name,
        city_input=query,
    )
    data = await state.get_data()
    await message.answer(_format_summary(data), reply_markup=confirm_kb())
    await state.set_state(Onboarding.confirming)


@router.callback_query(Onboarding.confirming, F.data == "onb:save")
async def on_confirm_save(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    data = await state.get_data()
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        profile = BirthProfile(user_id=user.id)
        session.add(profile)
    profile.birth_date = date.fromisoformat(data["birth_date"])
    profile.birth_time = time.fromisoformat(data["birth_time"])
    profile.time_unknown = data["time_unknown"]
    profile.lat = data["lat"]
    profile.lon = data["lon"]
    profile.tz = data["tz"]
    profile.city_name = data.get("city_input") or data["city_display"]
    await session.commit()

    await state.clear()
    await call.message.answer(
        "Данные сохранены ✨\nВыбери, что тебя интересует:",
        reply_markup=main_menu(),
    )
    await call.answer("Сохранено")


@router.callback_query(Onboarding.confirming, F.data == "onb:restart")
async def on_confirm_restart(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Onboarding.waiting_for_date)
    await call.message.answer(
        "Хорошо, начнём заново. Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code>:"
    )
    await call.answer()


@router.callback_query(F.data == "cancel")
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("Отменено.", reply_markup=main_menu())
    await call.answer()
