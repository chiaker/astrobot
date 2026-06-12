from __future__ import annotations

from datetime import UTC, date, datetime, time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.geocoding import geocode_city
from astrobot.bot.keyboards import (
    astro_terms_kb,
    confirm_kb,
    final_confirm_kb,
    gender_kb,
    main_menu,
    name_skip_kb,
    time_unknown_kb,
)
from astrobot.bot.states import Onboarding
from astrobot.db.models import BirthProfile, HoroscopeCache, User

router = Router(name="onboarding")


def _format_birth_summary(data: dict) -> str:
    d = date.fromisoformat(data["birth_date"])
    t = time.fromisoformat(data["birth_time"])
    time_str = "неизвестно (солнечная карта)" if data["time_unknown"] else t.strftime("%H:%M")
    return (
        "<b>Проверь данные рождения:</b>\n"
        f"📅 Дата: {d.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {time_str}\n"
        f"📍 Место: {data['city_display']}\n"
        f"🌐 Часовой пояс: {data['tz']}"
    )


def _format_final_summary(data: dict) -> str:
    d = date.fromisoformat(data["birth_date"])
    t = time.fromisoformat(data["birth_time"])
    time_str = "неизвестно (солнечная карта)" if data["time_unknown"] else t.strftime("%H:%M")

    name = data.get("display_name") or "—"
    gender_map = {"m": "мужской", "f": "женский"}
    gender_str = gender_map.get(data.get("gender") or "", "не указан")
    terms = "да, с терминами ✨" if data.get("astro_terms", True) else "без терминов 💬"

    return (
        "<b>Всё в порядке? Проверь данные:</b>\n\n"
        f"👤 Имя: <b>{name}</b>\n"
        f"⚧ Обращение: <b>{gender_str}</b>\n\n"
        f"📅 Дата рождения: <b>{d.strftime('%d.%m.%Y')}</b>\n"
        f"⏰ Время: <b>{time_str}</b>\n"
        f"📍 Место: <b>{data['city_display']}</b>\n"
        f"🌐 Часовой пояс: <b>{data['tz']}</b>\n\n"
        f"🔭 Астротермины: <b>{terms}</b>"
    )


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    bot: Bot,
    is_new_user: bool = False,
) -> None:
    await state.clear()

    from astrobot.metrics import REFERRALS_REGISTERED
    from astrobot.referral import parse_start_arg, try_apply_referral

    ref_code = parse_start_arg(message.text)
    if ref_code and is_new_user:
        inviter = await try_apply_referral(session, user, ref_code)
        if inviter is not None:
            await session.commit()
            REFERRALS_REGISTERED.inc()
            await message.answer(
                "🎁 Друг тебя пригласил — я добавила <b>+2 бесплатных вопроса</b>. "
                "Когда захочешь — приходи спрашивать ✨"
            )
            try:
                await bot.send_message(
                    inviter.tg_user_id,
                    "🎁 По твоей реферальной ссылке зарегистрировался новый пользователь — "
                    "тебе <b>+2 бесплатных вопроса</b>! ✨",
                )
            except Exception:
                pass

    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await message.answer(
            "🌙 С возвращением. Звёзды ждали тебя — выбирай, что хочешь узнать ✨",
            reply_markup=main_menu(),
        )
        return

    from astrobot.legal.disclaimer import ONBOARDING_CONSENT

    # Pre-fill existing values so repeat-onboarding users can skip steps
    await state.update_data(
        display_name=user.display_name,
        gender=user.gender,
        astro_terms=user.astro_terms_enabled,
    )

    hint = f"\nСейчас: <b>{user.display_name}</b>." if user.display_name else ""
    await message.answer(
        "🌙 Здравствуй.\n\n"
        "Меня зовут <b>Астра</b>. Я читаю карты звёзд и расскажу о тебе то, "
        "что записано в небе при твоём рождении.\n\n"
        f"Сначала — как тебя зовут?{hint}\n\n"
        + ONBOARDING_CONSENT,
        reply_markup=name_skip_kb(),
    )
    await state.set_state(Onboarding.waiting_for_name)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Хорошо, отложила в сторону ✨", reply_markup=main_menu())


# ─── Шаг 1: имя ───────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_name)
async def on_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 1 or len(name) > 64:
        await message.answer("Напиши имя (до 64 символов), или нажми «Пропустить».")
        return
    await state.update_data(display_name=name)
    await _ask_gender(message, state)


@router.callback_query(Onboarding.waiting_for_name, F.data == "onb:name:skip")
async def on_name_skip(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _ask_gender(call.message, state)


async def _ask_gender(target, state: FSMContext) -> None:
    await state.set_state(Onboarding.choosing_gender)
    await target.answer("Как к тебе обращаться?", reply_markup=gender_kb())


# ─── Шаг 2: пол ───────────────────────────────────────────────────────────────

@router.callback_query(Onboarding.choosing_gender, F.data.startswith("onb:gender:"))
async def on_gender(call: CallbackQuery, state: FSMContext) -> None:
    value = call.data.split(":")[-1]
    if value in ("m", "f"):
        await state.update_data(gender=value)
    await call.answer()
    # After personal prefs → start collecting birth data
    await state.set_state(Onboarding.waiting_for_date)
    await call.message.answer(
        "Отлично! Теперь мне нужны данные для натальной карты ✨\n\n"
        "Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code> "
        "(например, <code>14.03.1990</code>):"
    )


# ─── Шаг 3: дата ──────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_date)
async def on_date(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        birth_date = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "Не понял дату. Нужно <code>DD.MM.YYYY</code>, например <code>14.03.1990</code>."
        )
        return

    if birth_date.year < 1900 or birth_date > date.today():
        await message.answer("Дата должна быть между 1900 годом и сегодняшним днём.")
        return

    await state.update_data(birth_date=birth_date.isoformat())
    await message.answer(
        "Хорошо ✨\n\n"
        "Теперь <b>время рождения</b> в формате <code>HH:MM</code> "
        "(например, <code>14:30</code>).\n\n"
        "Если точное время неизвестно — нажми кнопку ниже. "
        "Я тогда построю солнечную карту — она расскажет о тебе многое, "
        "но без домов и Асцендента.",
        reply_markup=time_unknown_kb(),
    )
    await state.set_state(Onboarding.waiting_for_time)


# ─── Шаг 4: время ─────────────────────────────────────────────────────────────

@router.callback_query(Onboarding.waiting_for_time, F.data == "time:unknown")
async def on_time_unknown(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(birth_time=time(12, 0).isoformat(), time_unknown=True)
    await call.message.answer(
        "Поняла, делаем солнечную карту 🌞\n\n"
        "<b>Город рождения</b> — например, <code>Москва</code> или <code>Новосибирск</code>:"
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
        "Отлично ✨\n\n"
        "<b>Город рождения</b> — например, <code>Москва</code> или <code>Новосибирск</code>:"
    )
    await state.set_state(Onboarding.waiting_for_city)


# ─── Шаг 5: город ─────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_city)
async def on_city(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer(
            "Слишком короткое название. Введи город, например <code>Москва</code>."
        )
        return

    progress = await message.answer("🔍 Ищу твой город на карте…")
    result = await geocode_city(session, query)
    await progress.delete()

    if result is None:
        await message.answer(
            "Не нашла такой город — попробуй иначе, "
            "например <code>Санкт-Петербург, Россия</code>"
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
    await message.answer(_format_birth_summary(data), reply_markup=confirm_kb())
    await state.set_state(Onboarding.confirming)


# ─── Шаг 6: подтверждение данных рождения ─────────────────────────────────────

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
    await call.answer("Данные рождения сохранены")

    # Next: ask about astro terms preference
    await _ask_astro_terms(call.message, state)


@router.callback_query(Onboarding.confirming, F.data == "onb:restart")
async def on_confirm_restart(call: CallbackQuery, state: FSMContext) -> None:
    # Go back to date (keep name/gender already in state)
    await state.set_state(Onboarding.waiting_for_date)
    await call.message.answer(
        "Хорошо. Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code>:"
    )
    await call.answer()


# ─── Шаг 7: астротермины ──────────────────────────────────────────────────────

async def _ask_astro_terms(target, state: FSMContext) -> None:
    await state.set_state(Onboarding.choosing_astro_terms)
    await target.answer(
        "Последний вопрос — как тебе удобнее читать ответы?\n\n"
        "<b>С астрологическими терминами</b> (квадратура, Марс в Овне, транзиты…) — "
        "если ты знаком с астрологией.\n\n"
        "<b>Без терминов</b> — Астра будет объяснять через качества и жизненные темы, "
        "без специальных слов. Подходит тем, кто только знакомится.",
        reply_markup=astro_terms_kb(),
    )


@router.callback_query(Onboarding.choosing_astro_terms, F.data.startswith("onb:terms:"))
async def on_astro_terms(call: CallbackQuery, state: FSMContext) -> None:
    enabled = call.data.endswith(":yes")
    await state.update_data(astro_terms=enabled)
    await call.answer()
    # Show final confirmation with all data
    await _show_final_confirm(call.message, state)


# ─── Шаг 8: финальное подтверждение всех данных ───────────────────────────────

async def _show_final_confirm(target, state: FSMContext) -> None:
    await state.set_state(Onboarding.final_confirm)
    data = await state.get_data()
    await target.answer(
        _format_final_summary(data),
        reply_markup=final_confirm_kb(),
    )


@router.callback_query(Onboarding.final_confirm, F.data == "onb:final:ok")
async def on_final_ok(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    data = await state.get_data()
    user.display_name = data.get("display_name") or None
    user.gender = data.get("gender") or None
    user.astro_terms_enabled = bool(data.get("astro_terms", True))
    if user.legal_agreed_at is None:
        user.legal_agreed_at = datetime.now(UTC)
    await session.commit()
    await state.clear()

    name_part = f", {user.display_name}" if user.display_name else ""
    await call.message.answer(
        f"🌙 Запомнила{name_part}. Твоя карта со мной — теперь спрашивай о чём угодно ✨",
        reply_markup=main_menu(),
    )
    await call.answer("Готово")


@router.callback_query(Onboarding.final_confirm, F.data == "onb:final:restart")
async def on_final_restart(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    # Delete birth profile saved at step 6 + stale horoscope cache
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await session.delete(profile)
    await session.execute(delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id))
    await session.commit()

    # Clear and restart from the very beginning (name first)
    await state.clear()
    await state.update_data(
        display_name=user.display_name,
        gender=user.gender,
        astro_terms=user.astro_terms_enabled,
    )
    await state.set_state(Onboarding.waiting_for_name)
    hint = f" Сейчас: <b>{user.display_name}</b>." if user.display_name else ""
    await call.message.answer(
        f"Хорошо, начнём сначала. Как тебя зовут?{hint}",
        reply_markup=name_skip_kb(),
    )
    await call.answer()


# ─── cancel ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def on_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.answer("Хорошо, отложила ✨", reply_markup=main_menu())
    await call.answer()
