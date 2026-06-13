from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.keyboards import natal_paywall_kb
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.limits import check_natal, consume_natal_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_natal, split_brief_full

_REGEN_ROW = [InlineKeyboardButton(text="🔄 Пересчитать заново", callback_data="natal:regen")]

router = Router(name="natal")


def _profile_to_birth(profile: BirthProfile, name: str = "User") -> BirthData:
    return BirthData(
        name=name,
        date=profile.birth_date,
        time=profile.birth_time,
        time_unknown=profile.time_unknown,
        lat=profile.lat,
        lon=profile.lon,
        tz=profile.tz,
        city_name=profile.city_name,
    )


@router.callback_query(F.data == "menu:natal")
async def on_natal(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    target = call.message
    profile = await need_profile(target, session, user)
    if profile is None:
        return

    if profile.cached_natal_brief and profile.cached_natal_full:
        await save_and_send_response(
            target,
            session,
            user,
            "natal",
            profile.cached_natal_brief,
            profile.cached_natal_full,
            extra_row=_REGEN_ROW,
        )
        return

    allowance = await check_natal(session, user)
    if not allowance.allowed:
        await target.answer(paywall_text("natal", allowance), reply_markup=natal_paywall_kb())
        return

    pre_call_used = allowance.used
    progress = await target.answer("🌙 Слушаю, что говорят звёзды о тебе…")
    display_name = user.display_name or (call.from_user.full_name if call.from_user else None) or "User"
    birth = _profile_to_birth(profile, name=display_name)
    chart = build_natal_chart(birth)
    cached_context = chart_to_markdown(chart)

    await progress.edit_text("✨ Складываю узор твоей карты…")

    llm = get_llm()
    response = await llm.complete(
        system=build_system_natal(user),
        cached_context=cached_context,
        user_message="Дай интерпретацию натальной карты.",
        max_tokens=4500,
        kind="natal",
    )
    brief, full = split_brief_full(response.text)

    profile.cached_natal_brief = brief
    profile.cached_natal_full = full
    consume_natal_bonus_if_needed(user, pre_call_used)

    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind="natal",
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )

    await progress.delete()
    await save_and_send_response(target, session, user, "natal", brief, full, extra_row=_REGEN_ROW)


@router.callback_query(F.data == "natal:regen")
async def on_natal_regen(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.answer("Профиль не найден. Пройди /start.", show_alert=True)
        return

    allowance = await check_natal(session, user)
    if not allowance.allowed:
        await call.answer()
        await call.message.answer(paywall_text("natal", allowance), reply_markup=natal_paywall_kb())
        return

    profile.cached_natal_brief = None
    profile.cached_natal_full = None
    await session.commit()

    await call.answer()
    pre_call_used = allowance.used
    progress = await call.message.answer("🌙 Пересчитываю твою карту заново…")

    display_name = user.display_name or (call.from_user.full_name if call.from_user else None) or "User"
    birth = _profile_to_birth(profile, name=display_name)
    chart = build_natal_chart(birth)
    cached_context = chart_to_markdown(chart)

    await progress.edit_text("✨ Складываю узор твоей карты…")

    llm = get_llm()
    response = await llm.complete(
        system=build_system_natal(user),
        cached_context=cached_context,
        user_message="Дай интерпретацию натальной карты.",
        max_tokens=4500,
        kind="natal",
    )
    brief, full = split_brief_full(response.text)

    profile.cached_natal_brief = brief
    profile.cached_natal_full = full
    consume_natal_bonus_if_needed(user, pre_call_used)

    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind="natal",
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )

    await progress.delete()
    await save_and_send_response(call.message, session, user, "natal", brief, full, extra_row=_REGEN_ROW)
