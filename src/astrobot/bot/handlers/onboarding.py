from __future__ import annotations

import re
from datetime import UTC, date, datetime, time

import structlog
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.geocoding import geocode_city
from astrobot.bot.handlers.menu import show_main_menu
from astrobot.bot.keyboards import (
    astro_terms_kb,
    confirm_kb,
    final_confirm_kb,
    gender_kb,
    name_skip_kb,
    time_unknown_kb,
)
from astrobot.bot.platform import Media, PlatformBot, PlatformContext
from astrobot.bot.states import Onboarding
from astrobot.bot.utils import rate_limit_ok
from astrobot.config import get_settings
from astrobot.db.models import BirthProfile, HoroscopeCache, User
from astrobot.gender import guess_gender

log = structlog.get_logger(__name__)
router = Router(name="onboarding")

_NAME_RE = re.compile(r"^[^\W\d_]+(?:[-'][^\W\d_]+)*$", re.UNICODE)

_NAME_HINT = (
    "Напиши, пожалуйста, только <b>имя</b> — одним словом, без лишних символов, "
    "цифр и команд (например: <i>Олег</i>). Или нажми «Пропустить»."
)

GEOCODE_PER_HOUR = 20


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
        f"⚧ Пол: <b>{gender_str}</b>\n\n"
        f"📅 Дата рождения: <b>{d.strftime('%d.%m.%Y')}</b>\n"
        f"⏰ Время: <b>{time_str}</b>\n"
        f"📍 Место: <b>{data['city_display']}</b>\n"
        f"🌐 Часовой пояс: <b>{data['tz']}</b>\n\n"
        f"🔭 Астротермины: <b>{terms}</b>"
    )


def _welcome_media(value: str) -> Media:
    # A direct URL works on both platforms; a Telegram file_id only on Telegram.
    return Media.from_url(value) if value.startswith("http") else Media.from_file_id(value)


async def prompt_for_name(ctx: PlatformContext, state, user: User) -> None:
    """Send the welcome + 'how should I call you?' prompt and enter the name step."""
    from astrobot.legal.disclaimer import ONBOARDING_CONSENT

    await state.update_data(
        display_name=user.display_name,
        gender=user.gender,
        astro_terms=user.astro_terms_enabled,
    )
    hint = f"\nСейчас: <b>{user.display_name}</b>." if user.display_name else ""
    welcome_text = (
        "🌙 Здравствуй.\n\n"
        "Меня зовут <b>Астра</b>. Я читаю карты звёзд и расскажу о тебе то, "
        "что записано в небе при твоём рождении.\n\n"
        f"Сначала — как тебя зовут?{hint}\n\n"
        + ONBOARDING_CONSENT
    )
    animation = get_settings().welcome_animation
    if animation:
        try:
            await ctx.send_animation(_welcome_media(animation), caption=welcome_text, kb=name_skip_kb())
        except Exception as e:
            log.warning("welcome_animation_failed", error=str(e))
            await ctx.reply(welcome_text, name_skip_kb())
    else:
        await ctx.reply(welcome_text, name_skip_kb())
    await state.set_state(Onboarding.waiting_for_name)


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(
    ctx: PlatformContext,
    state,
    session: AsyncSession,
    user: User,
    pbot: PlatformBot,
    is_new_user: bool = False,
) -> None:
    await state.clear()

    from astrobot.metrics import REFERRALS_REGISTERED
    from astrobot.referral import parse_start_arg, try_apply_referral

    ref_code = parse_start_arg(ctx.text)
    if ref_code and is_new_user:
        applied = await try_apply_referral(session, user, ref_code)
        if applied is not None:
            inviter, inviter_credited = applied
            await session.commit()
            REFERRALS_REGISTERED.inc()
            await ctx.reply(
                "🎁 Друг тебя пригласил — я добавила <b>+2 бесплатных вопроса</b>. "
                "Когда захочешь — приходи спрашивать ✨"
            )
            if inviter_credited:
                try:
                    await pbot.send_message(
                        inviter.tg_user_id,
                        "🎁 По твоей реферальной ссылке зарегистрировался новый пользователь — "
                        "тебе <b>+2 бесплатных вопроса</b>! ✨",
                    )
                except Exception:
                    pass

    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await ctx.reply("🌙 С возвращением. Звёзды ждали тебя ✨")
        await show_main_menu(ctx, user, session)
        return

    await prompt_for_name(ctx, state, user)


@router.message(Command("cancel"))
async def cmd_cancel(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await state.clear()
    await ctx.reply("Хорошо, отложила в сторону ✨")
    await show_main_menu(ctx, user, session)


# ─── Шаг 1: имя ───────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_name)
async def on_name(ctx: PlatformContext, state) -> None:
    name = (ctx.text or "").strip()
    name = "".join(ch for ch in name if ch not in "<>" and (ch == " " or ch.isprintable()))
    name = name.strip()
    if len(name) > 64 or not _NAME_RE.match(name):
        await ctx.reply(_NAME_HINT, name_skip_kb())
        return
    await state.update_data(display_name=name)

    guessed = guess_gender(name)
    if guessed is not None:
        await state.update_data(gender=guessed)
        await _ask_date(ctx, state)
    else:
        await _ask_gender(ctx, state)


@router.callback_query(Onboarding.waiting_for_name, F.data == "onb:name:skip")
async def on_name_skip(ctx: PlatformContext, state) -> None:
    await ctx.answer_callback()
    await _ask_gender(ctx, state)


async def _ask_gender(ctx: PlatformContext, state) -> None:
    await state.set_state(Onboarding.choosing_gender)
    await ctx.reply("Укажи свой пол:", gender_kb())


async def _ask_date(ctx: PlatformContext, state) -> None:
    await state.set_state(Onboarding.waiting_for_date)
    await ctx.reply(
        "Отлично! Теперь мне нужны данные для натальной карты ✨\n\n"
        "Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code> "
        "(например, <code>14.03.1990</code>):"
    )


# ─── Шаг 2: пол ───────────────────────────────────────────────────────────────

@router.callback_query(Onboarding.choosing_gender, F.data.startswith("onb:gender:"))
async def on_gender(ctx: PlatformContext, state) -> None:
    value = (ctx.payload or "").split(":")[-1]
    if value in ("m", "f"):
        await state.update_data(gender=value)
    await ctx.answer_callback()
    await _ask_date(ctx, state)


# ─── Шаг 3: дата ──────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_date)
async def on_date(ctx: PlatformContext, state) -> None:
    text = (ctx.text or "").strip()
    try:
        birth_date = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        await ctx.reply(
            "Не понял дату. Нужно <code>DD.MM.YYYY</code>, например <code>14.03.1990</code>."
        )
        return

    if birth_date.year < 1900 or birth_date > date.today():
        await ctx.reply("Дата должна быть между 1900 годом и сегодняшним днём.")
        return

    await state.update_data(birth_date=birth_date.isoformat())
    await ctx.reply(
        "Хорошо ✨\n\n"
        "Теперь <b>время рождения</b> в формате <code>HH:MM</code> "
        "(например, <code>14:30</code>).\n\n"
        "Если точное время неизвестно — нажми кнопку ниже. "
        "Я тогда построю солнечную карту — она расскажет о тебе многое, "
        "но без домов и Асцендента.",
        time_unknown_kb(),
    )
    await state.set_state(Onboarding.waiting_for_time)


# ─── Шаг 4: время ─────────────────────────────────────────────────────────────

@router.callback_query(Onboarding.waiting_for_time, F.data == "time:unknown")
async def on_time_unknown(ctx: PlatformContext, state) -> None:
    await state.update_data(birth_time=time(12, 0).isoformat(), time_unknown=True)
    await ctx.reply(
        "Поняла, делаем солнечную карту 🌞\n\n"
        "<b>Город рождения</b> — например, <code>Москва</code> или <code>Новосибирск</code>:"
    )
    await state.set_state(Onboarding.waiting_for_city)
    await ctx.answer_callback()


@router.message(Onboarding.waiting_for_time)
async def on_time(ctx: PlatformContext, state) -> None:
    text = (ctx.text or "").strip()
    try:
        birth_time = datetime.strptime(text, "%H:%M").time()
    except ValueError:
        await ctx.reply(
            "Не понял время. Нужно <code>HH:MM</code>, например <code>14:30</code>. "
            "Или нажми кнопку «Не знаю точного времени»."
        )
        return

    await state.update_data(birth_time=birth_time.isoformat(), time_unknown=False)
    await ctx.reply(
        "Отлично ✨\n\n"
        "<b>Город рождения</b> — например, <code>Москва</code> или <code>Новосибирск</code>:"
    )
    await state.set_state(Onboarding.waiting_for_city)


# ─── Шаг 5: город ─────────────────────────────────────────────────────────────

@router.message(Onboarding.waiting_for_city)
async def on_city(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    query = (ctx.text or "").strip()
    if len(query) < 2:
        await ctx.reply("Слишком короткое название. Введи город, например <code>Москва</code>.")
        return

    if not await rate_limit_ok(f"geo:rl:{user.id}", GEOCODE_PER_HOUR, 3600):
        await ctx.reply("⏳ Слишком много запросов городов подряд. Попробуй через несколько минут.")
        return

    await ctx.reply("🔍 Ищу твой город на карте…")
    result = await geocode_city(session, query)

    if result is None:
        await ctx.reply(
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
    await ctx.reply(_format_birth_summary(data), confirm_kb())
    await state.set_state(Onboarding.confirming)


# ─── Шаг 6: подтверждение данных рождения ─────────────────────────────────────

@router.callback_query(Onboarding.confirming, F.data == "onb:save")
async def on_confirm_save(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    if "birth_date" not in data:
        # Stale confirm button (state was cleared, e.g. a fresh /start mid-flow).
        await ctx.answer_callback()
        await ctx.reply("Что-то сбилось — начнём заново. Нажми /start.")
        await state.clear()
        return
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
    await ctx.answer_callback("Данные рождения сохранены")

    await _ask_astro_terms(ctx, state)


@router.callback_query(Onboarding.confirming, F.data == "onb:restart")
async def on_confirm_restart(ctx: PlatformContext, state) -> None:
    await state.set_state(Onboarding.waiting_for_date)
    await ctx.reply("Хорошо. Введи <b>дату рождения</b> в формате <code>DD.MM.YYYY</code>:")
    await ctx.answer_callback()


# ─── Шаг 7: астротермины ──────────────────────────────────────────────────────

async def _ask_astro_terms(ctx: PlatformContext, state) -> None:
    await state.set_state(Onboarding.choosing_astro_terms)
    await ctx.reply(
        "Последний вопрос — как тебе удобнее читать ответы?\n\n"
        "<b>С астрологическими терминами</b> (квадратура, Марс в Овне, транзиты…) — "
        "если ты знаком с астрологией.\n\n"
        "<b>Без терминов</b> — Астра будет объяснять через качества и жизненные темы, "
        "без специальных слов. Подходит тем, кто только знакомится.",
        astro_terms_kb(),
    )


@router.callback_query(Onboarding.choosing_astro_terms, F.data.startswith("onb:terms:"))
async def on_astro_terms(ctx: PlatformContext, state) -> None:
    enabled = (ctx.payload or "").endswith(":yes")
    await state.update_data(astro_terms=enabled)
    await ctx.answer_callback()
    await _show_final_confirm(ctx, state)


# ─── Шаг 8: финальное подтверждение ───────────────────────────────────────────

async def _show_final_confirm(ctx: PlatformContext, state) -> None:
    await state.set_state(Onboarding.final_confirm)
    data = await state.get_data()
    await ctx.reply(_format_final_summary(data), final_confirm_kb())


@router.callback_query(Onboarding.final_confirm, F.data == "onb:final:ok")
async def on_final_ok(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    user.display_name = data.get("display_name") or None
    user.gender = data.get("gender") or None
    user.astro_terms_enabled = bool(data.get("astro_terms", True))
    if user.legal_agreed_at is None:
        user.legal_agreed_at = datetime.now(UTC)
    await session.commit()
    await state.clear()

    from astrobot.bot.handlers.natal import generate_natal

    await ctx.answer_callback("Готово")

    name_part = f", {user.display_name}" if user.display_name else ""
    await ctx.reply(f"🌙 Запомнила{name_part} ✨")
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await generate_natal(ctx, session, user, profile)
    else:
        await show_main_menu(ctx, user, session)


@router.callback_query(Onboarding.final_confirm, F.data == "onb:final:restart")
async def on_final_restart(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await session.delete(profile)
    await session.execute(delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id))
    await session.commit()

    await state.clear()
    await state.update_data(
        display_name=user.display_name,
        gender=user.gender,
        astro_terms=user.astro_terms_enabled,
    )
    await state.set_state(Onboarding.waiting_for_name)
    hint = f" Сейчас: <b>{user.display_name}</b>." if user.display_name else ""
    await ctx.reply(f"Хорошо, начнём сначала. Как тебя зовут?{hint}", name_skip_kb())
    await ctx.answer_callback()


# ─── cancel ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def on_cancel(ctx: PlatformContext, state, session: AsyncSession, user: User) -> None:
    await state.clear()
    await ctx.reply("Хорошо, отложила ✨")
    await show_main_menu(ctx, user, session)
    await ctx.answer_callback()
