from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete

from astrobot.bot.keyboards import MENU_PROFILE
from astrobot.bot.states import Onboarding
from astrobot.db.models import BirthProfile, HoroscopeCache, User
from astrobot.limits import check_horoscope, check_question, is_premium

router = Router(name="profile")


def _profile_kb(default_response: str) -> InlineKeyboardMarkup:
    mode = "кратко" if default_response == "brief" else "подробно"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📏 Ответы по умолчанию: {mode}",
                    callback_data="settings:response_toggle",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Ввести данные заново", callback_data="profile:reset"
                )
            ],
        ]
    )


async def _profile_text(profile: BirthProfile, user: User, session: AsyncSession) -> str:
    time_str = (
        "неизвестно (солнечная карта)"
        if profile.time_unknown
        else profile.birth_time.strftime("%H:%M")
    )
    base = (
        "<b>Твой профиль</b>\n"
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
    await message.answer(text, reply_markup=_profile_kb(user.default_response))


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
    await call.message.edit_text(text, reply_markup=_profile_kb(user.default_response))
    mode_label = "кратко" if user.default_response == "brief" else "подробно"
    await call.answer(f"Теперь по умолчанию — {mode_label}")


@router.callback_query(F.data == "profile:reset")
async def on_profile_reset(
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
