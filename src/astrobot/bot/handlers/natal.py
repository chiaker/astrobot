from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.keyboards import MENU_NATAL
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.utils import need_profile
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import SYSTEM_NATAL, split_brief_full

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

    progress = await message.answer("Считаю карту…")
    birth = _profile_to_birth(profile, name=message.from_user.full_name or "User")
    chart = build_natal_chart(birth)
    cached_context = chart_to_markdown(chart)

    await progress.edit_text("Готовлю интерпретацию…")

    llm = get_llm()
    response = await llm.complete(
        system=SYSTEM_NATAL,
        cached_context=cached_context,
        user_message="Дай интерпретацию натальной карты.",
        max_tokens=2500,
        kind="natal",
    )
    brief, full = split_brief_full(response.text)

    profile.cached_natal_brief = brief
    profile.cached_natal_full = full

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
