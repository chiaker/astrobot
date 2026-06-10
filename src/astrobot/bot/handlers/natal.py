from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.keyboards import MENU_NATAL, natal_paywall_kb
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.limits import check_natal, consume_natal_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_natal, split_brief_full

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


@router.message(F.text == MENU_NATAL)
async def on_natal(message: Message, session: AsyncSession, user: User) -> None:
    profile = await need_profile(message, session, user)
    if profile is None:
        return

    if profile.cached_natal_brief and profile.cached_natal_full:
        await save_and_send_response(
            message,
            session,
            user,
            "natal",
            profile.cached_natal_brief,
            profile.cached_natal_full,
        )
        return

    allowance = await check_natal(session, user)
    if not allowance.allowed:
        await message.answer(paywall_text("natal", allowance), reply_markup=natal_paywall_kb())
        return

    pre_call_used = allowance.used
    progress = await message.answer("🌙 Слушаю, что говорят звёзды о тебе…")
    display_name = user.display_name or (message.from_user.full_name if message.from_user else None) or "User"
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
    await save_and_send_response(message, session, user, "natal", brief, full)
