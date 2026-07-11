from __future__ import annotations

import asyncio

from aiogram import F, Router
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.keyboards import natal_cta_kb, natal_paywall_kb
from astrobot.bot.platform import Button, PlatformContext
from astrobot.bot.responses import send_response
from astrobot.bot.utils import need_profile_ctx, user_llm_lock
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.limits import check_natal, consume_natal_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_natal

_REGEN_ROW = [Button(text="🔄 Пересчитать заново", payload="natal:regen")]

_NATAL_CTA_TEXT = (
    "Вот мы и посмотрели самую поверхностную характеристику по твоей карте. "
    "Уже отлично! 💫 Думаю, нам стоит покопаться глубже\n\n"
    'Чтобы выбрать вопрос, нажми ниже на кнопку "Вопросы" либо выбери '
    "соответствующий пункт в меню. В бесплатном тарифе у тебя есть возможность "
    "спросить меня 2 раза.\n\n"
    "Я готова помочь тебе раскрыть новые грани твоей карты и найти ответы, "
    "которые действительно важны. 🙏"
)

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


async def _run_natal_generation(
    ctx: PlatformContext,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
    display_name: str,
    pre_call_used: int,
    show_actions: bool = True,
) -> None:
    """LLM call + cache update + send result."""
    birth = _profile_to_birth(profile, name=display_name)
    # CPU-bound (swisseph) — offload to a thread so it doesn't block the loop.
    chart = await asyncio.to_thread(build_natal_chart, birth)
    cached_context = chart_to_markdown(chart)

    llm = get_llm()
    response = await llm.complete(
        system=build_system_natal(user),
        cached_context=cached_context,
        user_message="Дай интерпретацию натальной карты.",
        max_tokens=4500,
        kind="natal",
    )
    text = response.text

    profile.cached_natal_brief = text
    profile.cached_natal_full = text
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
    await send_response(ctx, session, user, "natal", text, extra_row=_REGEN_ROW, show_actions=show_actions)


async def generate_natal(
    ctx: PlatformContext,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
) -> None:
    """Generate and send the natal chart right after onboarding."""
    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            return  # a generation is already running for this user
        await session.refresh(user)
        allowance = await check_natal(session, user)
        if not allowance.allowed:
            await ctx.reply(paywall_text("natal", allowance), natal_paywall_kb())
            return
        pre_call_used = allowance.used
        display_name = user.display_name or "User"
        await ctx.reply("🌙 Слушаю, что говорят звёзды о тебе…")
        # No action keyboard on the onboarding chart — the CTA message provides nav.
        await _run_natal_generation(
            ctx, session, user, profile, display_name, pre_call_used, show_actions=False
        )
        await ctx.reply(_NATAL_CTA_TEXT, natal_cta_kb())


@router.callback_query(F.data == "menu:natal")
async def on_natal(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    profile = await need_profile_ctx(ctx, session, user)
    if profile is None:
        return

    if profile.cached_natal_full:
        await send_response(ctx, session, user, "natal", profile.cached_natal_full, extra_row=_REGEN_ROW)
        return

    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.reply("⏳ Секунду — карта ещё считается.")
            return
        await session.refresh(user)
        allowance = await check_natal(session, user)
        if not allowance.allowed:
            await ctx.reply(paywall_text("natal", allowance), natal_paywall_kb())
            return
        display_name = user.display_name or "User"
        await ctx.reply("🌙 Слушаю, что говорят звёзды о тебе…")
        await _run_natal_generation(ctx, session, user, profile, display_name, allowance.used)


@router.callback_query(F.data == "natal:regen")
async def on_natal_regen(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await ctx.answer_callback("Профиль не найден. Пройди /start.", alert=True)
        return

    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await ctx.answer_callback("⏳ Уже пересчитываю — секунду…", alert=True)
            return
        await session.refresh(user)
        allowance = await check_natal(session, user)
        if not allowance.allowed:
            await ctx.answer_callback()
            await ctx.reply(paywall_text("natal", allowance), natal_paywall_kb())
            return

        profile.cached_natal_brief = None
        profile.cached_natal_full = None
        await session.commit()

        await ctx.answer_callback()
        display_name = user.display_name or "User"
        await ctx.reply("🌙 Пересчитываю твою карту заново…")
        await _run_natal_generation(ctx, session, user, profile, display_name, allowance.used)
