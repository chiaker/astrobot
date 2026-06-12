from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.astrology.chart import build_natal_chart
from astrobot.astrology.serializer import chart_to_markdown
from astrobot.astrology.transits import (
    build_transit_report,
    midnight_today_in,
    transit_report_to_markdown,
)
from astrobot.bot.formatting import md_to_telegram_html
from astrobot.bot.handlers.horoscope import _period_label
from astrobot.bot.handlers.natal import _profile_to_birth
from astrobot.config import get_settings
from astrobot.db.models import (
    BirthProfile,
    HoroscopeCache,
    LLMUsageLog,
    LunarEvent,
    Payment,
    User,
)
from astrobot.db.session import get_sessionmaker
from astrobot.limits import is_premium
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_horoscope, split_brief_full
from astrobot.lunar import compute_phases, horizon_dates, phase_text
from astrobot.metrics import PUSH_SENT
from astrobot.payments import service as payment_service

if TYPE_CHECKING:
    from aiogram import Bot

log = structlog.get_logger(__name__)


async def _get_or_generate_horoscope(
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
) -> tuple[str, str]:
    """Returns (brief, full) for today's horoscope, using cache or generating."""
    birth = _profile_to_birth(profile, name="User")
    today = midnight_today_in(birth.tz)

    cached = await session.scalar(
        select(HoroscopeCache).where(
            HoroscopeCache.user_id == user.id,
            HoroscopeCache.period == "today",
        )
    )
    if cached and cached.computed_for == today:
        return cached.brief, cached.full

    chart = await asyncio.to_thread(build_natal_chart, birth)
    natal_md = chart_to_markdown(chart)
    report = await asyncio.to_thread(build_transit_report, birth, today, "today")
    transits_md = transit_report_to_markdown(report)

    llm = get_llm()
    response = await llm.complete(
        system=build_system_horoscope(user),
        cached_context=natal_md + "\n\n" + transits_md,
        user_message="Дай гороскоп на сегодня.",
        max_tokens=2800,
        kind="horoscope_today",
    )
    brief, full = split_brief_full(response.text)

    if cached:
        cached.computed_for = today
        cached.brief = brief
        cached.full = full
    else:
        session.add(
            HoroscopeCache(
                user_id=user.id,
                period="today",
                computed_for=today,
                brief=brief,
                full=full,
            )
        )

    session.add(
        LLMUsageLog(
            user_id=user.id,
            kind="horoscope:today",
            model=response.model,
            input_tokens=response.input_tokens,
            cached_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
        )
    )
    return brief, full


def _user_local_hour(tz: str) -> int:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).hour


def _user_local_date(tz: str):
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date()


def _push_tz(user: User, profile: BirthProfile) -> str:
    """Timezone to use for push timing: user's current city first, birth city as fallback."""
    return user.push_tz or profile.tz


def _push_hour(user: User, settings) -> int:
    """Push hour in user's local time."""
    return user.push_hour if user.push_hour is not None else settings.push_horoscope_hour


async def morning_horoscope_job(bot: Bot) -> None:
    """Runs every minute. Finds premium opted-in users whose local time hit
    the push hour, hasn't been pushed today, sends their daily horoscope."""
    settings = get_settings()
    target_hour = settings.push_horoscope_hour
    now_utc = datetime.now(UTC)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        users = list(
            await session.scalars(
                select(User)
                .where(User.push_horoscope_enabled.is_(True))
                .where(User.premium_until.isnot(None))
                .where(User.premium_until > now_utc)
            )
        )
        for user in users:
            if not is_premium(user):
                continue
            profile = await session.get(BirthProfile, user.id)
            if profile is None or not profile.tz:
                continue

            tz = _push_tz(user, profile)
            target = _push_hour(user, settings)
            try:
                if _user_local_hour(tz) != target:
                    continue
                today_local = _user_local_date(tz)
            except Exception as e:
                log.warning("tz_check_failed", user_id=user.id, error=str(e))
                continue

            if user.last_horoscope_push_at is not None:
                try:
                    from zoneinfo import ZoneInfo

                    last_local = user.last_horoscope_push_at.astimezone(ZoneInfo(tz)).date()
                    if last_local >= today_local:
                        continue
                except Exception:
                    pass

            try:
                brief, full = await _get_or_generate_horoscope(session, user, profile)
            except Exception as e:
                log.warning("push_generate_failed", user_id=user.id, error=str(e))
                PUSH_SENT.labels(kind="horoscope", result="fail").inc()
                continue

            label = _period_label("today", today_local)
            text = (
                "🌅 <b>Доброе утро.</b> Звёзды для тебя на сегодня:\n\n"
                f"{label}\n\n" + md_to_telegram_html(brief)
            )
            try:
                await bot.send_message(
                    chat_id=user.tg_user_id,
                    text=text[:4000],
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                user.last_horoscope_push_at = now_utc
                await session.commit()
                PUSH_SENT.labels(kind="horoscope", result="ok").inc()
                log.info("push_horoscope_sent", user_id=user.id)
            except Exception as e:
                await session.rollback()
                log.warning("push_send_failed", user_id=user.id, error=str(e))
                PUSH_SENT.labels(kind="horoscope", result="fail").inc()


async def refresh_lunar_events_job() -> None:
    """Daily: compute new/full moon dates for the next 30 days and upsert
    them into the lunar_events table."""
    sessionmaker = get_sessionmaker()
    start, end = horizon_dates()
    phases = compute_phases(start, end)
    async with sessionmaker() as session:
        for phase in phases:
            existing = await session.scalar(
                select(LunarEvent).where(LunarEvent.event_date == phase.event_date)
            )
            if existing is None:
                session.add(
                    LunarEvent(
                        event_date=phase.event_date,
                        kind=phase.kind,
                        notified=False,
                    )
                )
        await session.commit()
        log.info("lunar_events_refreshed", count=len(phases))


async def lunar_push_job(bot: Bot) -> None:
    """Per-minute: if today is a lunar event, push to opted-in premium
    users at their local push hour."""
    settings = get_settings()
    target_hour = settings.push_horoscope_hour
    now_utc = datetime.now(UTC)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        users = list(
            await session.scalars(
                select(User)
                .where(User.push_lunar_enabled.is_(True))
                .where(User.premium_until.isnot(None))
                .where(User.premium_until > now_utc)
            )
        )
        for user in users:
            if not is_premium(user):
                continue
            profile = await session.get(BirthProfile, user.id)
            if profile is None or not profile.tz:
                continue
            tz = _push_tz(user, profile)
            target = _push_hour(user, settings)
            try:
                if _user_local_hour(tz) != target:
                    continue
                today_local = _user_local_date(tz)
            except Exception:
                continue

            event = await session.scalar(
                select(LunarEvent).where(LunarEvent.event_date == today_local)
            )
            if event is None:
                continue

            try:
                await bot.send_message(
                    chat_id=user.tg_user_id,
                    text=phase_text(event.kind),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                PUSH_SENT.labels(kind="lunar", result="ok").inc()
                log.info("push_lunar_sent", user_id=user.id, kind=event.kind)
            except Exception as e:
                log.warning("push_lunar_failed", user_id=user.id, error=str(e))
                PUSH_SENT.labels(kind="lunar", result="fail").inc()


async def reconcile_payments_job(bot: Bot) -> None:
    """Safety net for missed webhooks: poll YooKassa for every pending payment
    and apply its real status (grant / cancel / refund). Pending payments older
    than 2h that still can't be resolved are marked canceled (abandoned)."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(hours=2)
    orphan_before = now - timedelta(minutes=30)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        pendings = list(
            await session.scalars(
                select(Payment).where(
                    Payment.status == "pending",
                    Payment.yookassa_payment_id.isnot(None),
                )
            )
        )
        for payment in pendings:
            try:
                result = await payment_service.reconcile_payment(session, payment, bot)
            except Exception as e:
                log.warning("reconcile_payment_error", payment_id=payment.id, error=str(e))
                continue
            # Unresolved + old → consider abandoned, stop showing as pending
            if result in {"pending", "error", "mismatch"} and payment.created_at < stale_before:
                payment.status = "canceled"
                await session.commit()
                log.info("payment_marked_abandoned", payment_id=payment.id)

        # Orphans: created but YooKassa payment id never persisted (crash between
        # create and save) — they can never be reconciled. Cancel old ones.
        orphans = list(
            await session.scalars(
                select(Payment).where(
                    Payment.status == "pending",
                    Payment.yookassa_payment_id.is_(None),
                    Payment.created_at < orphan_before,
                )
            )
        )
        for payment in orphans:
            payment.status = "canceled"
        if orphans:
            await session.commit()
            log.info("orphan_pendings_canceled", count=len(orphans))


async def premium_expiry_reminder_job(bot: Bot) -> None:
    """Hourly: remind premium users whose subscription ends within N days.
    Deduped via premium_reminded_until so each expiry is reminded once; a
    renewal (new premium_until) re-arms the reminder."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    settings = get_settings()
    now = datetime.now(UTC)
    horizon = now + timedelta(days=settings.premium_reminder_days_before)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        users = list(
            await session.scalars(
                select(User)
                .where(User.premium_until.isnot(None))
                .where(User.premium_until > now)
                .where(User.premium_until <= horizon)
            )
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💎 Продлить премиум", callback_data="premium:show")]
            ]
        )
        for user in users:
            if user.premium_reminded_until == user.premium_until:
                continue  # already reminded for this expiry
            until = user.premium_until.strftime("%d.%m.%Y") if user.premium_until else ""
            try:
                await bot.send_message(
                    chat_id=user.tg_user_id,
                    text=(
                        f"💎 Твой премиум заканчивается <b>{until}</b>.\n\n"
                        "Продли, чтобы не потерять 3 гороскопа в день, 10 вопросов в месяц "
                        "и утренние пуши ✨"
                    ),
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                user.premium_reminded_until = user.premium_until
                await session.commit()
                PUSH_SENT.labels(kind="premium_expiry", result="ok").inc()
                log.info("premium_reminder_sent", user_id=user.id)
            except Exception as e:
                await session.rollback()
                log.warning("premium_reminder_failed", user_id=user.id, error=str(e))
                PUSH_SENT.labels(kind="premium_expiry", result="fail").inc()


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone="UTC")
    sched.add_job(
        morning_horoscope_job,
        trigger="cron",
        minute="*",
        args=[bot],
        id="morning_horoscope",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        lunar_push_job,
        trigger="cron",
        minute="*",
        args=[bot],
        id="lunar_push",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        refresh_lunar_events_job,
        trigger="cron",
        hour=0,
        minute=10,
        id="lunar_refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    # Run once on startup so the table is populated even before midnight
    sched.add_job(
        refresh_lunar_events_job,
        trigger="date",
        id="lunar_refresh_bootstrap",
        replace_existing=True,
    )
    sched.add_job(
        reconcile_payments_job,
        trigger="cron",
        minute="*/5",
        args=[bot],
        id="reconcile_payments",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        premium_expiry_reminder_job,
        trigger="cron",
        minute=0,
        args=[bot],
        id="premium_expiry_reminder",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return sched
