from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.types import BirthData
from astrobot.bot.keyboards import natal_cta_kb, natal_paywall_kb
from astrobot.bot.responses import save_and_send_response
from astrobot.bot.utils import need_profile, user_llm_lock
from astrobot.db.models import BirthProfile, LLMUsageLog, User
from astrobot.limits import check_natal, consume_natal_bonus_if_needed, paywall_text
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_natal

_REGEN_ROW = [InlineKeyboardButton(text="🔄 Пересчитать заново", callback_data="natal:regen")]

# Shown once, right after the first (onboarding) natal chart — nudges the user
# toward asking a question or exploring plans.
_NATAL_CTA_TEXT = (
    "Вот мы и посмотрели самую поверхностную характеристику по твоей карте. "
    "Уже отлично! 💫 Думаю, нам стоит покопаться глубже\n\n"
    'Чтобы выбрать вопрос, нажми ниже на кнопку "Вопросы" либо выбери '
    "соответствующий пункт в меню. В бесплатном тарифе у тебя есть возможность "
    "спросить меня 2 раза. Еще ты можешь задать свой вопрос, выбрав платный тариф.\n\n"
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
    target: Message,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
    display_name: str,
    pre_call_used: int,
    progress: Message,
    show_actions: bool = True,
) -> None:
    """LLM call + cache update + send result. Progress message is deleted after."""
    birth = _profile_to_birth(profile, name=display_name)
    # CPU-bound (swisseph) — offload to a thread so it doesn't block the
    # single event loop while other users are being served.
    chart = await asyncio.to_thread(build_natal_chart, birth)
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

    await progress.delete()
    await save_and_send_response(
        target, session, user, "natal", text, extra_row=_REGEN_ROW, show_actions=show_actions
    )


async def generate_natal(
    target: Message,
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
) -> None:
    """Generate and send natal chart without paywall/cache checks.

    Called after onboarding so the chart appears immediately after data entry.
    """
    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            return  # a generation is already running for this user
        await session.refresh(user)
        pre_call_used = (await check_natal(session, user)).used
        display_name = user.display_name or "User"
        progress = await target.answer("🌙 Слушаю, что говорят звёзды о тебе…")
        # No action keyboard on the onboarding chart — the CTA message sent
        # right after already provides navigation buttons.
        await _run_natal_generation(
            target, session, user, profile, display_name, pre_call_used, progress,
            show_actions=False,
        )
        # Call-to-action after the very first chart (onboarding only).
        await target.answer(_NATAL_CTA_TEXT, reply_markup=natal_cta_kb())


@router.callback_query(F.data == "menu:natal")
async def on_natal(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    target = call.message
    profile = await need_profile(target, session, user)
    if profile is None:
        return

    if profile.cached_natal_full:
        await save_and_send_response(
            target, session, user, "natal", profile.cached_natal_full, extra_row=_REGEN_ROW
        )
        return

    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await target.answer("⏳ Секунду — карта ещё считается.")
            return
        await session.refresh(user)
        allowance = await check_natal(session, user)
        if not allowance.allowed:
            await target.answer(paywall_text("natal", allowance), reply_markup=natal_paywall_kb())
            return

        display_name = user.display_name or (call.from_user.full_name if call.from_user else None) or "User"
        progress = await target.answer("🌙 Слушаю, что говорят звёзды о тебе…")
        await _run_natal_generation(target, session, user, profile, display_name, allowance.used, progress)


@router.callback_query(F.data == "natal:regen")
async def on_natal_regen(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    profile = await session.get(BirthProfile, user.id)
    if profile is None:
        await call.answer("Профиль не найден. Пройди /start.", show_alert=True)
        return

    async with user_llm_lock(user.id) as acquired:
        if not acquired:
            await call.answer("⏳ Уже пересчитываю — секунду…", show_alert=True)
            return
        await session.refresh(user)
        allowance = await check_natal(session, user)
        if not allowance.allowed:
            await call.answer()
            await call.message.answer(paywall_text("natal", allowance), reply_markup=natal_paywall_kb())
            return

        profile.cached_natal_brief = None
        profile.cached_natal_full = None
        await session.commit()

        await call.answer()
        display_name = user.display_name or (call.from_user.full_name if call.from_user else None) or "User"
        progress = await call.message.answer("🌙 Пересчитываю твою карту заново…")
        await _run_natal_generation(call.message, session, user, profile, display_name, allowance.used, progress)
