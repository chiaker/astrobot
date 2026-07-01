from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile
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
from astrobot.bot.keyboards import build_broadcast_kb, followup_cta_kb
from astrobot.config import get_settings
from astrobot.db.models import (
    BirthProfile,
    Broadcast,
    BroadcastVariant,
    HoroscopeCache,
    LLMUsageLog,
    LunarEvent,
    Payment,
    Subscription,
    User,
)
from astrobot.db.session import get_sessionmaker
from astrobot.limits import is_premium, segment_of
from astrobot.llm.client import get_llm
from astrobot.llm.prompts import build_system_horoscope
from astrobot.lunar import compute_phases, horizon_dates, phase_text
from astrobot.metrics import PUSH_SENT
from astrobot.payments import service as payment_service
from astrobot.payments import yookassa
from astrobot.payments.catalog import build_receipt, get_item
from astrobot.redis_client import get_redis

if TYPE_CHECKING:
    from aiogram import Bot

log = structlog.get_logger(__name__)


async def _get_or_generate_horoscope(
    session: AsyncSession,
    user: User,
    profile: BirthProfile,
) -> str:
    """Returns today's horoscope text, using cache or generating."""
    birth = _profile_to_birth(profile, name="User")
    today = midnight_today_in(birth.tz)

    cached = await session.scalar(
        select(HoroscopeCache).where(
            HoroscopeCache.user_id == user.id,
            HoroscopeCache.period == "today",
        )
    )
    if cached and cached.computed_for == today:
        return cached.full

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
    text = response.text

    if cached:
        cached.computed_for = today
        cached.brief = text
        cached.full = text
    else:
        session.add(
            HoroscopeCache(
                user_id=user.id,
                period="today",
                computed_for=today,
                brief=text,
                full=text,
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
    return text


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
                full = await _get_or_generate_horoscope(session, user, profile)
            except Exception as e:
                log.warning("push_generate_failed", user_id=user.id, error=str(e))
                PUSH_SENT.labels(kind="horoscope", result="fail").inc()
                continue

            label = _period_label("today", today_local)
            text = (
                "🌅 <b>Доброе утро.</b> Звёзды для тебя на сегодня:\n\n"
                f"{label}\n\n" + md_to_telegram_html(full)
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

            # Dedup: the job runs every minute, so without this guard we'd send
            # once per minute for the whole push hour. One push per user per event.
            dedup_key = f"lunar:pushed:{user.id}:{today_local.isoformat()}"
            try:
                fresh = await get_redis().set(dedup_key, "1", ex=2 * 24 * 3600, nx=True)
            except Exception:
                fresh = True  # Redis down — don't block, but may resend
            if not fresh:
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
                payment.cancel_reason = "timeout"
                await session.commit()
                log.info("payment_marked_abandoned", payment_id=payment.id)

        # Orphans: a YooKassa payment created but whose payment id never persisted
        # (crash between create and save) — can never be reconciled. Cancel old ones.
        orphans = list(
            await session.scalars(
                select(Payment).where(
                    Payment.status == "pending",
                    Payment.provider != "telegram_stars",
                    Payment.yookassa_payment_id.is_(None),
                    Payment.created_at < orphan_before,
                )
            )
        )
        for payment in orphans:
            payment.status = "canceled"
            payment.cancel_reason = "orphan"
        if orphans:
            await session.commit()
            log.info("orphan_pendings_canceled", count=len(orphans))

        # Telegram Stars: the user has no "cancel" button and the invoice has no
        # external status to poll, so an unpaid Stars invoice would sit pending
        # forever. Mark abandoned ones canceled. Harmless if paid later — the
        # successful_payment grant still applies (it only short-circuits on
        # an already-succeeded payment, not a canceled one).
        stars_pending = list(
            await session.scalars(
                select(Payment).where(
                    Payment.status == "pending",
                    Payment.provider == "telegram_stars",
                    Payment.created_at < stale_before,
                )
            )
        )
        for payment in stars_pending:
            payment.status = "canceled"
            payment.cancel_reason = "timeout"
        if stars_pending:
            await session.commit()
            log.info("stars_pendings_canceled", count=len(stars_pending))


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
        # Users with an active auto-renewing subscription don't get a "renew
        # manually" nudge — it renews on its own.
        active_sub_users = select(Subscription.user_id).where(
            Subscription.status == "active"
        )
        users = list(
            await session.scalars(
                select(User)
                .where(User.premium_until.isnot(None))
                .where(User.premium_until > now)
                .where(User.premium_until <= horizon)
                .where(User.id.notin_(active_sub_users))
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


async def _cancel_failed_subscription(
    session: AsyncSession, sub: Subscription, user: User, bot: Bot
) -> None:
    """One-attempt policy: a failed renewal charge cancels the subscription.
    Premium keeps running until current_period_end, then lapses."""
    sub.status = "canceled"
    sub.next_charge_at = None
    sub.canceled_at = datetime.now(UTC)
    await session.commit()
    log.info("subscription_charge_failed_canceled", user_id=user.id)
    try:
        await bot.send_message(
            chat_id=user.tg_user_id,
            text=(
                "⚠️ Не получилось продлить премиум — списание с карты не прошло.\n\n"
                "Автоподписка отключена. Премиум останется активным до конца "
                "оплаченного срока. Оформить заново можно в разделе 💎 Премиум."
            ),
        )
    except Exception as e:
        log.warning("sub_cancel_notify_failed", user_id=user.id, error=str(e))


async def charge_due_card_subscriptions_job(bot: Bot) -> None:
    """Hourly: charge YooKassa card subscriptions whose period is ending, using
    the saved card token. One attempt per cycle — on decline/error the
    subscription is canceled (per product decision). Stars subscriptions renew on
    Telegram's side and are not handled here."""
    now = datetime.now(UTC)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        subs = list(
            await session.scalars(
                select(Subscription).where(
                    Subscription.provider == "yookassa",
                    Subscription.status == "active",
                    Subscription.next_charge_at.isnot(None),
                    Subscription.next_charge_at <= now,
                )
            )
        )
        for sub in subs:
            user = await session.get(User, sub.user_id)
            item = get_item(sub.plan_code)
            if user is None or item is None or not sub.yookassa_payment_method_id:
                continue

            # Clear next_charge_at up front so an overlapping run can't double
            # charge; a successful reconcile re-arms it via upsert_subscription.
            sub.next_charge_at = None
            payment = Payment(
                user_id=user.id,
                provider="yookassa",
                item_code=item.code,
                kind=item.kind,
                amount=item.amount_rub,
                currency="RUB",
                status="pending",
                email=user.email,
            )
            session.add(payment)
            await session.flush()

            try:
                resp = await yookassa.create_recurring_payment(
                    amount_rub=item.amount_rub,
                    description=f"{item.title} — Астра (автопродление)",
                    metadata={
                        "payment_id": str(payment.id),
                        "tg_user_id": str(user.tg_user_id),
                        "item_code": item.code,
                    },
                    receipt=build_receipt(user.email or "", item),
                    payment_method_id=sub.yookassa_payment_method_id,
                )
            except Exception as e:
                log.warning("subscription_charge_error", user_id=user.id, error=str(e))
                payment.status = "canceled"
                payment.cancel_reason = "create_error"
                await _cancel_failed_subscription(session, sub, user, bot)
                continue

            payment.yookassa_payment_id = resp.get("id")
            await session.commit()
            status = resp.get("status")

            if status in {"succeeded", "pending", "waiting_for_capture"}:
                # reconcile_payment fetches the authoritative state, grants on
                # success, and re-arms next_charge_at via upsert_subscription.
                # "pending" is left for the 5-min reconcile job to finish.
                try:
                    result = await payment_service.reconcile_payment(session, payment, bot)
                except Exception as e:
                    log.warning("subscription_reconcile_error", user_id=user.id, error=str(e))
                    result = "error"
                if result in {"canceled", "error", "mismatch"}:
                    await _cancel_failed_subscription(session, sub, user, bot)
            else:
                await _cancel_failed_subscription(session, sub, user, bot)


# ─── Day-2 (48h after registration) follow-up broadcast ───────────────────────

FOLLOWUP_DELAY_HOURS = 48
FOLLOWUP_BATCH = 300            # max users handled per run (spreads big backlogs)
FOLLOWUP_SEND_DELAY = 0.05     # ~20 msg/s, well under Telegram's global limit

_FOLLOWUP_TEXT = (
    "Мы уже прикоснулись к самым ярким граням твоей натальной карты и "
    "результаты впечатляют! ✨\n\n"
    "Но это лишь начало увлекательного путешествия к более глубокому пониманию "
    "себя.\n\n"
    "Давай заглянем ещё глубже и я отвечу на вопросы, которые действительно "
    "важны для тебя.\n\n"
    "Чтобы выбрать интересующую тему, нажми кнопку «Вопросы» ниже или выбери "
    "нужный раздел в меню."
)


async def _send_followup(bot: Bot, chat_id: int, animation: str) -> None:
    """Send the follow-up: animation+caption if configured (falling back to text
    on a bad file_id), else plain text. Lets TelegramRetryAfter/Forbidden bubble up."""
    kb = followup_cta_kb()
    if animation:
        try:
            await bot.send_animation(
                chat_id=chat_id, animation=animation, caption=_FOLLOWUP_TEXT, reply_markup=kb
            )
            return
        except (TelegramRetryAfter, TelegramForbiddenError):
            raise
        except Exception as e:
            log.warning("followup_animation_failed", chat_id=chat_id, error=str(e))
    await bot.send_message(chat_id=chat_id, text=_FOLLOWUP_TEXT, reply_markup=kb)


async def day2_followup_job(bot: Bot) -> None:
    """Once per user, ~48h after registration: send the follow-up nudge. Only to
    users who completed onboarding (have a BirthProfile), since the copy talks
    about their natal chart. Deduped via User.followup_sent_at."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=FOLLOWUP_DELAY_HOURS)
    animation = get_settings().followup_animation

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        users = list(
            await session.scalars(
                select(User)
                .where(
                    User.followup_sent_at.is_(None),
                    User.created_at <= cutoff,
                    User.id.in_(select(BirthProfile.user_id)),
                )
                .order_by(User.created_at)
                .limit(FOLLOWUP_BATCH)
            )
        )
        for user in users:
            try:
                await _send_followup(bot, user.tg_user_id, animation)
                user.followup_sent_at = now
                await session.commit()
                PUSH_SENT.labels(kind="followup", result="ok").inc()
                log.info("followup_sent", user_id=user.id)
                await asyncio.sleep(FOLLOWUP_SEND_DELAY)
            except TelegramRetryAfter as e:
                # We're being rate-limited — back off and finish the rest next run.
                await session.rollback()
                log.warning("followup_rate_limited", seconds=e.retry_after)
                await asyncio.sleep(e.retry_after + 0.5)
                break
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                # Bot blocked / chat unavailable — mark sent so we don't retry forever.
                await session.rollback()
                user.followup_sent_at = now
                await session.commit()
                PUSH_SENT.labels(kind="followup", result="fail").inc()
                log.info("followup_undeliverable", user_id=user.id, error=str(e))
            except Exception as e:
                # Unknown error — mark sent to avoid an infinite retry loop.
                await session.rollback()
                user.followup_sent_at = now
                await session.commit()
                PUSH_SENT.labels(kind="followup", result="fail").inc()
                log.warning("followup_failed", user_id=user.id, error=str(e))


# ─── Admin-authored broadcast campaigns ───────────────────────────────────────

BROADCAST_BATCH = 300          # users scanned per pass (resumable via cursor)
BROADCAST_SEND_DELAY = 0.05    # ~20 msg/s, well under Telegram's global limit


def _variant_has_content(variant) -> bool:
    # animation_name is set whenever an animation was uploaded and is a small,
    # always-loaded column — so this never needs the (potentially deferred) blob.
    return bool(
        (variant.text or "").strip()
        or (variant.animation or "").strip()
        or variant.animation_name
    )


async def _send_broadcast_variant(bot: Bot, chat_id: int, variant) -> str | None:
    """Send a broadcast variant: animation+caption if configured, else plain text.
    Returns a freshly obtained Telegram file_id when an uploaded file was sent for
    the first time (so the caller can cache it into `variant.animation` and skip
    re-uploading on later sends), otherwise None. Lets TelegramRetryAfter/Forbidden
    bubble up to the per-user handler."""
    kb = build_broadcast_kb(variant)
    text = variant.text or ""

    # Fast path: a known file_id (cached from a prior send) or legacy URL.
    if variant.animation:
        try:
            await bot.send_animation(
                chat_id=chat_id, animation=variant.animation, caption=text, reply_markup=kb
            )
            return None
        except (TelegramRetryAfter, TelegramForbiddenError):
            raise
        except Exception as e:
            log.warning("broadcast_animation_failed", chat_id=chat_id, error=str(e))

    # Uploaded bytes with no cached file_id yet: upload once, return the file_id.
    elif variant.animation_data:
        upload = BufferedInputFile(
            bytes(variant.animation_data), filename=variant.animation_name or "animation.mp4"
        )
        try:
            msg = await bot.send_animation(
                chat_id=chat_id, animation=upload, caption=text, reply_markup=kb
            )
            media = msg.animation or msg.document or msg.video
            return media.file_id if media else None
        except (TelegramRetryAfter, TelegramForbiddenError):
            raise
        except Exception as e:
            log.warning("broadcast_animation_upload_failed", chat_id=chat_id, error=str(e))

    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
    return None


async def _run_broadcast(bot: Bot, broadcast_id: int) -> None:
    """Drain one 'sending' broadcast in resumable batches. Each user is committed
    individually (cursor + counters) so a crash never double-sends, and a
    rate-limit pauses the campaign until the next dispatch tick."""
    sessionmaker = get_sessionmaker()
    while True:
        async with sessionmaker() as session:
            broadcast = await session.get(Broadcast, broadcast_id)
            if broadcast is None or broadcast.status != "sending":
                return
            # Load variants with an explicit query — the lazy `broadcast.variants`
            # relationship can't be accessed in async context.
            variant_rows = await session.scalars(
                select(BroadcastVariant).where(
                    BroadcastVariant.broadcast_id == broadcast_id
                )
            )
            variants = {
                v.segment: v
                for v in variant_rows
                if v.enabled and _variant_has_content(v)
            }
            cursor = broadcast.cursor_user_id or 0
            users = list(
                await session.scalars(
                    select(User)
                    .where(User.id > cursor)
                    .order_by(User.id)
                    .limit(BROADCAST_BATCH)
                )
            )
            if not users:
                broadcast.status = "sent"
                broadcast.sent_at = datetime.now(UTC)
                await session.commit()
                log.info(
                    "broadcast_done",
                    broadcast_id=broadcast_id,
                    sent=broadcast.sent_count,
                    failed=broadcast.failed_count,
                )
                return

            ids = [u.id for u in users]
            onboarded = set(
                await session.scalars(
                    select(BirthProfile.user_id).where(BirthProfile.user_id.in_(ids))
                )
            )
            for user in users:
                variant = variants.get(segment_of(user, user.id in onboarded))
                if variant is not None:
                    try:
                        new_file_id = await _send_broadcast_variant(
                            bot, user.tg_user_id, variant
                        )
                        # Cache the file_id after the first upload so the rest of
                        # the batch reuses it instead of re-uploading the bytes.
                        if new_file_id and not variant.animation:
                            variant.animation = new_file_id
                        broadcast.sent_count += 1
                        PUSH_SENT.labels(kind="broadcast", result="ok").inc()
                    except TelegramRetryAfter as e:
                        # Don't advance the cursor past this user — retry next run.
                        await session.rollback()
                        log.warning("broadcast_rate_limited", seconds=e.retry_after)
                        await asyncio.sleep(e.retry_after + 0.5)
                        return
                    except (TelegramForbiddenError, TelegramBadRequest) as e:
                        broadcast.failed_count += 1
                        PUSH_SENT.labels(kind="broadcast", result="fail").inc()
                        log.info("broadcast_undeliverable", user_id=user.id, error=str(e))
                    except Exception as e:
                        broadcast.failed_count += 1
                        PUSH_SENT.labels(kind="broadcast", result="fail").inc()
                        log.warning("broadcast_failed", user_id=user.id, error=str(e))
                    await asyncio.sleep(BROADCAST_SEND_DELAY)
                broadcast.cursor_user_id = user.id
                await session.commit()


async def broadcast_dispatch_job(bot: Bot) -> None:
    """Promote due scheduled broadcasts to 'sending', then drain any in progress."""
    now = datetime.now(UTC)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        due = list(
            await session.scalars(
                select(Broadcast).where(
                    Broadcast.status == "scheduled",
                    Broadcast.scheduled_at.isnot(None),
                    Broadcast.scheduled_at <= now,
                )
            )
        )
        for b in due:
            b.status = "sending"
        if due:
            await session.commit()

        sending_ids = list(
            await session.scalars(
                select(Broadcast.id)
                .where(Broadcast.status == "sending")
                .order_by(Broadcast.id)
            )
        )

    for broadcast_id in sending_ids:
        await _run_broadcast(bot, broadcast_id)


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
    sched.add_job(
        day2_followup_job,
        trigger="cron",
        minute="*/15",
        args=[bot],
        id="day2_followup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        charge_due_card_subscriptions_job,
        trigger="cron",
        minute=30,
        args=[bot],
        id="charge_card_subs",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    sched.add_job(
        broadcast_dispatch_job,
        trigger="cron",
        minute="*",
        args=[bot],
        id="broadcast_dispatch",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return sched
