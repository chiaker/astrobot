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
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_PROFILE, reset_confirm_kb
from astrobot.bot.states import Onboarding
from astrobot.db.models import BirthProfile, Favorite, HoroscopeCache, LLMUsageLog, User
from astrobot.limits import NATAL_REGEN_PRICE_RUB, check_horoscope, check_question, is_premium

router = Router(name="profile")


def _profile_kb(user: User) -> InlineKeyboardMarkup:
    mode = "кратко" if user.default_response == "brief" else "подробно"
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"📏 Ответы по умолчанию: {mode}",
                callback_data="settings:response_toggle",
            )
        ],
    ]
    if is_premium(user):
        horo_state = "вкл" if user.push_horoscope_enabled else "выкл"
        lunar_state = "вкл" if user.push_lunar_enabled else "выкл"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🌅 Утренний гороскоп: {horo_state}",
                    callback_data="settings:push_horoscope",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🌑 Лунные фазы: {lunar_state}",
                    callback_data="settings:push_lunar",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🤝 Пригласить друга", callback_data="referral:show"
            )
        ]
    )
    terms_state = "вкл" if user.astro_terms_enabled else "выкл"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🔭 Астротермины: {terms_state}",
                callback_data="settings:astro_terms",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Ввести данные заново", callback_data="profile:reset"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    if is_premium(user) and user.premium_until:
        until = user.premium_until.strftime("%d.%m.%Y")
        return base + (
            f"💎 <b>Премиум до {until}</b>\n"
            "Звёзды в твоём распоряжении ✨"
        )

    q_allow = await check_question(session, user)
    h_allow = await check_horoscope(session, user)
    q_left = max(0, q_allow.limit - q_allow.used)
    h_left = max(0, h_allow.limit - h_allow.used)
    return base + (
        "🆓 <b>Бесплатный тариф</b>\n"
        f"💬 Вопросов осталось: <b>{q_left} из {q_allow.limit}</b>\n"
        f"🔮 Гороскоп сегодня: {'доступен ✨' if h_left > 0 else 'на сегодня посмотрен'}\n\n"
        "<i>💎 Премиум снимает границы — загляни в раздел «Премиум».</i>"
    )


@router.message(F.text == MENU_PROFILE)
async def on_profile(message: Message, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await message.answer(
            "У тебя ещё нет сохранённого профиля. Нажми /start, чтобы пройти онбординг."
        )
        return
    text = await _profile_text(profile, user, session)
    await message.answer(text, reply_markup=_profile_kb(user))


@router.callback_query(F.data == "settings:response_toggle")
async def on_response_toggle(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    user.default_response = "full" if user.default_response == "brief" else "brief"
    await session.commit()
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.answer()
        return
    text = await _profile_text(profile, user, session)
    await call.message.edit_text(text, reply_markup=_profile_kb(user))
    mode_label = "кратко" if user.default_response == "brief" else "подробно"
    await call.answer(f"Теперь по умолчанию — {mode_label}")


@router.callback_query(F.data == "settings:push_horoscope")
async def on_push_horoscope_toggle(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    if not is_premium(user):
        await call.answer("Доступно только в Премиуме", show_alert=True)
        return
    user.push_horoscope_enabled = not user.push_horoscope_enabled
    await session.commit()
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        text = await _profile_text(profile, user, session)
        await call.message.edit_text(text, reply_markup=_profile_kb(user))
    state_label = "включён" if user.push_horoscope_enabled else "выключен"
    await call.answer(f"Утренний гороскоп {state_label}")


@router.callback_query(F.data == "settings:push_lunar")
async def on_push_lunar_toggle(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    if not is_premium(user):
        await call.answer("Доступно только в Премиуме", show_alert=True)
        return
    user.push_lunar_enabled = not user.push_lunar_enabled
    await session.commit()
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        text = await _profile_text(profile, user, session)
        await call.message.edit_text(text, reply_markup=_profile_kb(user))
    state_label = "включены" if user.push_lunar_enabled else "выключены"
    await call.answer(f"Лунные фазы {state_label}")


@router.callback_query(F.data == "settings:astro_terms")
async def on_astro_terms_toggle(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    user.astro_terms_enabled = not user.astro_terms_enabled
    await session.commit()
    profile = await session.get(BirthProfile, user.id)
    if profile is not None:
        text = await _profile_text(profile, user, session)
        await call.message.edit_text(text, reply_markup=_profile_kb(user))
    label = "включены" if user.astro_terms_enabled else "выключены"
    await call.answer(f"Астрологические термины {label}")


@router.callback_query(F.data == "profile:reset")
async def on_profile_reset_warn(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    """Show warnings before reset — natal limit, favorites count."""
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
        await session.scalar(
            select(func.count(Favorite.id)).where(Favorite.user_id == user.id)
        )
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
    await session.execute(
        delete(HoroscopeCache).where(HoroscopeCache.user_id == user.id)
    )
    await session.commit()
    await state.set_state(Onboarding.waiting_for_date)
    await call.message.answer(
        "Прежние данные удалены. Введи <b>дату рождения</b> в формате "
        "<code>DD.MM.YYYY</code>:"
    )
    await call.answer()
