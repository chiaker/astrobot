from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.geocoding import geocode_city
from astrobot.bot.keyboards import (
    MENU_BACK_BTN,
    horoscope_period_kb,
    name_skip_kb,
    push_hour_kb,
    reset_confirm_kb,
    with_back,
)
from astrobot.bot.responses import edit_or_send
from astrobot.bot.states import Onboarding, PushSetup
from astrobot.db.models import (
    BirthProfile,
    Favorite,
    HoroscopeCache,
    LLMUsageLog,
    Payment,
    User,
)
from astrobot.limits import NATAL_REGEN_PRICE_RUB, PREMIUM_LIMITS, check_horoscope, check_question, is_premium
from astrobot.payments.catalog import get_item

router = Router(name="profile")


_GENDER_LABEL = {"m": "мужской", "f": "женский"}


def _profile_kb(user: User) -> InlineKeyboardMarkup:
    gender_label = _GENDER_LABEL.get(user.gender or "", "не указан")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"⚧ Пол: {gender_label}", callback_data="settings:gender")],
            [InlineKeyboardButton(text="✏️ Изменить данные / сбросить", callback_data="profile:reset")],
            [InlineKeyboardButton(text="🧾 История операций", callback_data="payments:mine")],
            [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral:show")],
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:settings"),
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="menu:support"),
            ],
            [MENU_BACK_BTN],
        ]
    )


def _settings_kb(user: User) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    terms_state = "вкл" if user.astro_terms_enabled else "выкл"
    rows.append(
        [InlineKeyboardButton(text=f"🔭 Астротермины: {terms_state}", callback_data="settings:astro_terms")]
    )
    if is_premium(user):
        lunar_state = "вкл" if user.push_lunar_enabled else "выкл"
        rows.append(
            [InlineKeyboardButton(text=f"🌑 Лунные фазы: {lunar_state}", callback_data="settings:push_lunar")]
        )
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _settings_text(user: User) -> str:
    terms = "вкл (с терминами)" if user.astro_terms_enabled else "выкл (без терминов)"
    parts = [
        "⚙️ <b>Настройки</b>",
        "",
        f"🔭 Астрологические термины: <b>{terms}</b>",
    ]
    if not is_premium(user):
        parts.append(
            "\n<i>Уведомления (утренний гороскоп, лунные фазы) доступны в Премиуме.</i>"
        )
    return "\n".join(parts)


async def _render_settings(call: CallbackQuery, user: User) -> None:
    await edit_or_send(call, _settings_text(user), _settings_kb(user))


async def _profile_text(profile: BirthProfile, user: User, session: AsyncSession) -> str:
    time_str = (
        "неизвестно (солнечная карта)"
        if profile.time_unknown
        else profile.birth_time.strftime("%H:%M")
    )
    name_line = f"👤 Имя: {user.display_name}\n" if user.display_name else ""
    gender_map = {"m": "мужской", "f": "женский"}
    gender_line = (
        f"⚧ Пол: {gender_map.get(user.gender or '', 'не указан')}\n"
        if user.gender in gender_map
        else ""
    )
    base = (
        "<b>Твой профиль</b>\n"
        f"{name_line}"
        f"{gender_line}"
        f"📅 Дата: {profile.birth_date.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {time_str}\n"
        f"📍 Место: {profile.city_name}\n"
        f"🌐 Часовой пояс: {profile.tz}\n\n"
    )

    q_allow = await check_question(session, user)
    h_allow = await check_horoscope(session, user)
    q_left = max(0, q_allow.limit - q_allow.used)
    h_left = max(0, h_allow.limit - h_allow.used)

    if is_premium(user) and user.premium_until:
        until = user.premium_until.strftime("%d.%m.%Y")
        monthly_limit = PREMIUM_LIMITS.question_per_month or 0
        monthly_left = max(0, monthly_limit - q_allow.used)
        bonus = max(0, user.bonus_questions or 0)
        bonus_line = f"\n🎁 Доп. вопросы из пакета: <b>{bonus}</b> (не сгорают)" if bonus > 0 else ""
        return base + (
            f"💎 <b>Премиум до {until}</b>\n"
            f"💬 Вопросов в этом месяце: <b>{monthly_left} из {monthly_limit}</b>{bonus_line}\n"
            f"🔮 Гороскопов сегодня: <b>{h_left} из {h_allow.limit}</b>\n\n"
            "Звёзды в твоём распоряжении ✨"
        )

    return base + (
        "🆓 <b>Бесплатный тариф</b>\n"
        f"💬 Вопросов осталось: <b>{q_left} из {q_allow.limit}</b>\n"
        f"🔮 Гороскоп сегодня: {'доступен ✨' if h_left > 0 else 'на сегодня посмотрен'}\n\n"
        "<i>💎 Премиум открывает новые горизонты — загляни в раздел «Премиум».</i>"
    )


@router.callback_query(F.data == "menu:profile")
async def on_profile(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await edit_or_send(
            call,
            "У тебя ещё нет сохранённого профиля. Нажми /start, чтобы пройти онбординг.",
            with_back([]),
        )
        return
    text = await _profile_text(profile, user, session)
    await edit_or_send(call, text, _profile_kb(user))


@router.callback_query(F.data == "menu:settings")
async def on_settings(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    await _render_settings(call, user)


def _fmt_op(p: Payment) -> str:
    item = get_item(p.item_code)
    title = item.title if item else p.item_code
    amount = int(p.amount)
    if p.status == "succeeded":
        d = (p.paid_at or p.created_at).strftime("%d.%m.%Y")
        return f"✅ {d} · {title} · <b>{amount} ₽</b> — оплачен"
    if p.status == "pending":
        d = p.created_at.strftime("%d.%m.%Y")
        return f"⏳ {d} · {title} · {amount} ₽ — ожидает оплаты"
    d = p.created_at.strftime("%d.%m.%Y")
    return f"✖️ {d} · {title} · {amount} ₽ — отменён"


@router.callback_query(F.data == "payments:mine")
async def on_my_payments(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    payments = list(
        await session.scalars(
            select(Payment)
            .where(Payment.user_id == user.id, Payment.status != "refunded")
            .order_by(desc(Payment.created_at))
            .limit(30)
        )
    )
    if not payments:
        await edit_or_send(call, "🧾 У тебя пока нет операций.", with_back([]))
        await call.answer()
        return

    lines = ["🧾 <b>История операций</b>", ""]
    lines += [_fmt_op(p) for p in payments]
    lines += [
        "",
        "<i>Фискальный чек по каждому оплаченному платежу приходит на email.</i>",
    ]
    await edit_or_send(call, "\n".join(lines), with_back([]))
    await call.answer()


# ─── Push horoscope setup ─────────────────────────────────────────────────────

@router.callback_query(F.data == "settings:push_horoscope")
async def on_push_horoscope_toggle(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    if not is_premium(user):
        await call.answer("Доступно только в Премиуме", show_alert=True)
        return

    if user.push_horoscope_enabled:
        # Disable immediately
        user.push_horoscope_enabled = False
        await session.commit()
        await edit_or_send(call, "🔮 На какой период посмотрим?", horoscope_period_kb(user))
        await call.answer("Утренний гороскоп выключен")
        return

    # Enabling — check if already has push settings
    if user.push_tz and user.push_city_name:
        hour = user.push_hour if user.push_hour is not None else 9
        user.push_horoscope_enabled = True
        await session.commit()
        await edit_or_send(call, "🔮 На какой период посмотрим?", horoscope_period_kb(user))
        await call.answer(f"Включён · {hour}:00 · {user.push_city_name}")
        return

    # No push settings yet — start setup
    await call.answer()
    await state.set_state(PushSetup.waiting_for_city)
    await call.message.answer(
        "🌅 Настраиваем утренний гороскоп.\n\n"
        "Напиши, в каком городе ты сейчас живёшь — "
        "это нужно для точного определения часового пояса. "
        "Место рождения может не совпадать с текущим.\n\n"
        "<i>Например: Москва, Санкт-Петербург, Алматы</i>"
    )


@router.message(PushSetup.waiting_for_city)
async def on_push_city(message: Message, state: FSMContext, session: AsyncSession) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Напиши название города, например <code>Москва</code>.")
        return

    progress = await message.answer("🔍 Ищу город…")
    result = await geocode_city(session, query)
    await progress.delete()

    if result is None:
        await message.answer(
            "Не нашла такой город. Попробуй иначе, "
            "например <code>Санкт-Петербург, Россия</code>."
        )
        return

    await state.update_data(push_tz=result.tz, push_city_name=result.display_name)
    await state.set_state(PushSetup.choosing_hour)
    await message.answer(
        f"📍 Нашла: <b>{result.display_name}</b> (часовой пояс <code>{result.tz}</code>)\n\n"
        "В какое время тебе присылать утренний гороскоп?\n"
        "<i>Время указывается по твоему текущему городу.</i>",
        reply_markup=push_hour_kb(),
    )


@router.callback_query(PushSetup.choosing_hour, F.data.startswith("push:hour:"))
async def on_push_hour(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    hour = int(call.data.split(":")[-1])
    data = await state.get_data()
    user.push_tz = data["push_tz"]
    user.push_city_name = data["push_city_name"]
    user.push_hour = hour
    user.push_horoscope_enabled = True
    await session.commit()
    await state.clear()

    await call.message.edit_text(
        f"✅ Готово! Буду присылать утренний гороскоп в <b>{hour}:00</b> "
        f"по времени <b>{user.push_city_name}</b>.\n\n"
        "Можешь изменить настройки в профиле."
    )
    await call.answer("Настроено")


@router.callback_query(PushSetup.choosing_hour, F.data == "push:cancel")
@router.callback_query(PushSetup.waiting_for_city, F.data == "push:cancel")
async def on_push_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text("Настройка отменена.")
    await call.answer()


# ─── Push lunar toggle ────────────────────────────────────────────────────────

@router.callback_query(F.data == "settings:push_lunar")
async def on_push_lunar_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    if not is_premium(user):
        await call.answer("Доступно только в Премиуме", show_alert=True)
        return
    user.push_lunar_enabled = not user.push_lunar_enabled
    await session.commit()
    await _render_settings(call, user)
    label = "включены" if user.push_lunar_enabled else "выключены"
    await call.answer(f"Лунные фазы {label}")


# ─── Astro terms toggle ───────────────────────────────────────────────────────

@router.callback_query(F.data == "settings:gender")
async def on_gender_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    # Cycle: м → ж → не указан → м
    nxt = {"m": "f", "f": None}.get(user.gender or "", "m")
    user.gender = nxt
    # Cached natal/horoscope text uses gendered agreement → regenerate.
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        profile.cached_natal_brief = None
        profile.cached_natal_full = None
    await session.execute(delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id))
    await session.commit()
    if profile is not None:
        text = await _profile_text(profile, user, session)
        await edit_or_send(call, text, _profile_kb(user))
    await call.answer(f"Пол: {_GENDER_LABEL.get(user.gender or '', 'не указан')}")


@router.callback_query(F.data == "settings:astro_terms")
async def on_astro_terms_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    user.astro_terms_enabled = not user.astro_terms_enabled
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        profile.cached_natal_brief = None
        profile.cached_natal_full = None
    await session.execute(delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id))
    await session.commit()
    await _render_settings(call, user)
    label = "включены" if user.astro_terms_enabled else "выключены"
    await call.answer(f"Астрологические термины {label}")


# ─── Profile reset ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile:reset")
async def on_profile_reset_warn(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    natal_this_month = (
        await session.scalar(
            select(func.count(LLMUsageLog.id)).where(
                LLMUsageLog.user_id == user.id,
                LLMUsageLog.kind == "natal",
                LLMUsageLog.created_at >= datetime.now(UTC) - timedelta(days=30),
            )
        )
    ) or 0

    fav_count = (
        await session.scalar(select(func.count(Favorite.id)).where(Favorite.user_id == user.id))
    ) or 0

    lines = ["⚠️ <b>Сброс профиля</b>\n", "Это удалит твои данные рождения и настройки."]
    if natal_this_month > 0:
        lines.append(
            f"\n🌟 <b>Внимание:</b> натальная карта уже была рассчитана в этом месяце. "
            f"После сброса — новая генерация только через 30 дней "
            f"или за <b>{NATAL_REGEN_PRICE_RUB} ₽</b>."
        )
    if fav_count > 0:
        lines.append(f"\n⭐ Будет удалено <b>{fav_count}</b> записей из Избранного.")
    lines.append("\nПродолжить?")

    await call.message.answer("\n".join(lines), reply_markup=reset_confirm_kb())
    await call.answer()


@router.callback_query(F.data == "profile:reset:confirm")
async def on_profile_reset_confirm(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        await session.delete(profile)
    await session.execute(delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id))
    await session.commit()

    # Pre-fill existing prefs so the user doesn't re-enter name/gender/terms
    await state.update_data(
        display_name=user.display_name,
        gender=user.gender,
        astro_terms=user.astro_terms_enabled,
    )
    await state.set_state(Onboarding.waiting_for_name)
    hint = f" Сейчас: <b>{user.display_name}</b>." if user.display_name else ""
    await call.message.answer(
        f"Прежние данные удалены. Как тебя зовут?{hint}",
        reply_markup=name_skip_kb(),
    )
    await call.answer()
