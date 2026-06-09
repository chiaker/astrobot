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

from astrobot.bot.keyboards import MENU_PROFILE
from astrobot.bot.states import Onboarding
from astrobot.db.models import BirthProfile, User

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


def _profile_text(profile: BirthProfile) -> str:
    time_str = (
        "неизвестно (солнечная карта)"
        if profile.time_unknown
        else profile.birth_time.strftime("%H:%M")
    )
    return (
        "<b>Твой профиль:</b>\n"
        f"📅 Дата: {profile.birth_date.strftime('%d.%m.%Y')}\n"
        f"⏰ Время: {time_str}\n"
        f"📍 Место: {profile.city_name}\n"
        f"🌐 Часовой пояс: {profile.tz}"
    )


@router.message(F.text == MENU_PROFILE)
async def on_profile(message: Message, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await message.answer(
            "У тебя ещё нет сохранённого профиля. Нажми /start, чтобы пройти онбординг."
        )
        return
    await message.answer(_profile_text(profile), reply_markup=_profile_kb(user.default_response))


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
    await call.message.edit_text(
        _profile_text(profile), reply_markup=_profile_kb(user.default_response)
    )
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
        await session.commit()
    await state.set_state(Onboarding.waiting_for_date)
    await call.message.answer(
        "Прежние данные удалены. Введи <b>дату рождения</b> в формате "
        "<code>DD.MM.YYYY</code>:"
    )
    await call.answer()
