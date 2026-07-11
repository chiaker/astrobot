"""Shared payment side-effects: grant on success, revoke on refund, reconcile.

Used by the webhook (web/routes/payments.py), the reconciliation job
(scheduler.py) and the admin refund action (web/routes/stats.py), so the
granting/revoking logic lives in exactly one place and is idempotent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.platform import Button, Keyboard, PlatformBot
from astrobot.config import get_settings
from astrobot.db.models import LLMUsageLog, Payment, Subscription, User
from astrobot.limits import QUESTION_PACK_SIZE
from astrobot.metrics import PAYMENTS_REFUNDED, PAYMENTS_SUCCEEDED
from astrobot.payments import yookassa
from astrobot.payments.catalog import Item, get_item

if TYPE_CHECKING:
    from aiogram import Bot

log = structlog.get_logger(__name__)


def _confirmation_text(payment: Payment, user: User) -> str:
    if payment.kind == "subscription":
        until = user.premium_until.strftime("%d.%m.%Y") if user.premium_until else ""
        return (
            "✅ Оплата прошла — <b>Премиум активирован</b>"
            + (f" до <b>{until}</b>" if until else "")
            + ". Звёзды теперь открыты для тебя полностью ✨"
        )
    if payment.kind == "natal_regen":
        return (
            "✅ Оплата прошла — добавила <b>1 пересчёт натальной карты</b>. "
            "Нажми «🌟 Натальная карта» → «🔄 Пересчитать заново» ✨"
        )
    if payment.kind == "question_pack":
        return "✅ Оплата прошла — вопросы зачислены. Спрашивай Астру ✨"
    return "✅ Оплата прошла — спасибо ✨"


def _refund_text(payment: Payment) -> str:
    return (
        "↩️ Платёж возвращён. Соответствующие начисления отменены. "
        "Если это ошибка — напиши нам."
    )


_MENU_KB = Keyboard.from_rows([[Button(text="🔮 Открыть меню", payload="menu:open")]])


def _confirmation_kb(payment: Payment, user: User) -> Keyboard:
    """Post-purchase keyboard. After a subscription is granted, nudge the user to
    set up the morning horoscope (push:setup_start → profile.py) unless it's
    already on."""
    rows: list[list[Button]] = []
    if payment.kind == "subscription" and not user.push_horoscope_enabled:
        rows.append(
            [Button(text="🌅 Настроить утренний гороскоп", payload="push:setup_start")]
        )
    rows.append([Button(text="🔮 Открыть меню", payload="menu:open")])
    return Keyboard.from_rows(rows)


async def upsert_subscription(
    session: AsyncSession,
    user: User,
    *,
    provider: str,
    plan_code: str,
    period_end: datetime,
    payment_method_id: str | None = None,
    telegram_charge_id: str | None = None,
) -> Subscription:
    """Create or refresh the user's auto-renewing subscription after a first or
    renewal charge. One row per user (reused on re-subscribe). For YooKassa the
    next charge is scheduled at period_end; Stars renewals are driven by Telegram
    so next_charge_at stays NULL."""
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    if sub is None:
        sub = Subscription(user_id=user.id)
        session.add(sub)
    sub.provider = provider
    sub.plan_code = plan_code
    sub.status = "active"
    sub.current_period_end = period_end
    sub.next_charge_at = period_end if provider == "yookassa" else None
    sub.canceled_at = None
    if payment_method_id is not None:
        sub.yookassa_payment_method_id = payment_method_id
    if telegram_charge_id is not None:
        sub.telegram_charge_id = telegram_charge_id
    await session.commit()
    log.info("subscription_upserted", user_id=user.id, provider=provider, plan=plan_code)
    return sub


async def cancel_subscription(
    session: AsyncSession, user: User, bot: Bot | None
) -> Subscription | None:
    """Stop auto-renewal. Premium keeps running until current_period_end and then
    lapses. For Stars we also cancel the Telegram-side subscription so it won't
    recharge. Returns the subscription (or None if there was none)."""
    sub = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    if sub is None or sub.status != "active":
        return sub
    if sub.provider == "telegram_stars" and sub.telegram_charge_id and bot is not None:
        try:
            await bot.edit_user_star_subscription(
                user_id=user.tg_user_id,
                telegram_payment_charge_id=sub.telegram_charge_id,
                is_canceled=True,
            )
        except Exception as e:
            log.warning("stars_sub_cancel_failed", user_id=user.id, error=str(e))
    sub.status = "canceled"
    sub.next_charge_at = None
    sub.canceled_at = datetime.now(UTC)
    await session.commit()
    log.info("subscription_canceled", user_id=user.id, provider=sub.provider)
    return sub


async def _push(pbot: PlatformBot | None, user_id: int, text: str, kb: Keyboard | None = None) -> None:
    if pbot is None:
        return
    try:
        await pbot.send_message(user_id, text, kb)
    except Exception as e:
        log.warning("payment_push_failed", user_id=user_id, error=str(e))


async def _alert_ops_once(bot: PlatformBot | None, dedup_key: str, text: str) -> None:
    """Ops alert sent at most once per dedup_key, so the 5-min reconcile job can't
    spam the same 'paid but not granted' payment over and over."""
    from astrobot.alerts import notify_ops
    from astrobot.redis_client import get_redis

    try:
        fresh = await get_redis().set(f"alert:{dedup_key}", "1", ex=86400, nx=True)
    except Exception:
        fresh = True  # Redis down → still alert (better noisy than silent about money)
    if fresh:
        await notify_ops(bot, text)


async def grant_payment(session: AsyncSession, payment: Payment, pbot: PlatformBot | None) -> bool:
    """Idempotently grant the purchased item and notify. True if newly granted.

    Uses SELECT ... FOR UPDATE to serialize concurrent callers (webhook +
    reconciliation job) so the benefit is granted exactly once.
    """
    row = (
        await session.execute(
            select(Payment).where(Payment.id == payment.id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status == "succeeded":
        await session.commit()  # release the row lock
        return False

    now = datetime.now(UTC)
    item = get_item(row.item_code)
    user = await session.get(User, row.user_id)
    if item is None or user is None:
        # Money was paid — flip status anyway so it doesn't loop; flag it loudly.
        row.status = "succeeded"
        row.paid_at = now
        await session.commit()
        log.warning("grant_missing_item_or_user", payment_id=row.id, item=row.item_code)
        reason = "товар не найден" if item is None else "пользователь не найден"
        await _alert_ops_once(
            pbot,
            f"grant_missing:{row.id}",
            "🚨 Платёж ОПЛАЧЕН, но начисление не сделано — нужна ручная проверка.\n"
            f"payment_id={row.id}, user_id={row.user_id}, item={row.item_code}\n"
            f"Причина: {reason}.",
        )
        return False

    item.grant(user)
    row.status = "succeeded"
    row.paid_at = now
    await session.commit()
    PAYMENTS_SUCCEEDED.labels(item=row.item_code).inc()
    log.info("payment_granted", payment_id=row.id, item=row.item_code)

    await _push(
        pbot, user.tg_user_id, _confirmation_text(row, user),
        kb=_confirmation_kb(row, user),
    )
    return True


async def refund_payment(session: AsyncSession, payment: Payment, pbot: PlatformBot | None) -> bool:
    """Idempotently mark a payment refunded and revoke its benefit — but only
    revoke/notify if the benefit was actually granted (status was succeeded).

    SELECT ... FOR UPDATE serializes concurrent refunds (admin button +
    refund.succeeded webhook) so revoke happens at most once.
    """
    row = (
        await session.execute(
            select(Payment).where(Payment.id == payment.id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.status == "refunded":
        await session.commit()  # release the row lock
        return False

    was_granted = row.status == "succeeded"
    item = get_item(row.item_code)
    user = await session.get(User, row.user_id)
    if was_granted and user is not None and item is not None:
        item.revoke(user)
    row.status = "refunded"
    row.refunded_at = datetime.now(UTC)
    await session.commit()
    PAYMENTS_REFUNDED.labels(item=row.item_code).inc()
    log.info("payment_refunded", payment_id=row.id, item=row.item_code, revoked=was_granted)

    if was_granted and user is not None:
        await _push(pbot, user.tg_user_id, _refund_text(row))
    return True


async def _count_usage_since(
    session: AsyncSession, user_id: int, kind_prefix: str, since: datetime | None
) -> int:
    stmt = select(func.count(LLMUsageLog.id)).where(
        LLMUsageLog.user_id == user_id,
        LLMUsageLog.kind.like(f"{kind_prefix}%"),
    )
    if since is not None:
        stmt = stmt.where(LLMUsageLog.created_at >= since)
    return (await session.scalar(stmt)) or 0


async def consumed_fraction(session: AsyncSession, payment: Payment, item: Item) -> float:
    """How much of the purchase has been used, as a 0..1+ fraction.

    - subscription: elapsed time / period
    - question_pack: questions used since purchase / pack size
    - natal_regen: natal recalcs since purchase (1 unit → 1.0 per use)
    """
    paid = payment.paid_at or payment.created_at
    now = datetime.now(UTC)
    if item.kind == "subscription":
        if not item.duration_days or paid is None:
            return 0.0
        elapsed_days = (now - paid).total_seconds() / 86400
        return max(0.0, elapsed_days / item.duration_days)
    if item.kind == "question_pack":
        used = await _count_usage_since(session, payment.user_id, "question", paid)
        return used / QUESTION_PACK_SIZE if QUESTION_PACK_SIZE else 0.0
    if item.kind == "natal_regen":
        used = await _count_usage_since(session, payment.user_id, "natal", paid)
        return float(used)
    return 0.0


async def refund_eligibility(session: AsyncSession, payment: Payment) -> tuple[bool, str]:
    """Policy check: refundable only within the window AND if consumed <= threshold.
    Returns (allowed, human-readable reason when not allowed)."""
    settings = get_settings()
    now = datetime.now(UTC)

    ref = payment.paid_at or payment.created_at
    if ref is not None:
        age_days = (now - ref).total_seconds() / 86400
        if age_days > settings.refund_window_days:
            return False, f"прошло больше {settings.refund_window_days} дней с оплаты"

    item = get_item(payment.item_code)
    if item is None:
        return True, ""

    consumed = await consumed_fraction(session, payment, item)
    threshold = settings.refund_max_consumed_pct / 100.0
    if consumed > threshold:
        return (
            False,
            f"использовано ~{consumed * 100:.0f}% (порог {settings.refund_max_consumed_pct}%)",
        )
    return True, ""


async def reconcile_payment(session: AsyncSession, payment: Payment, pbot: PlatformBot | None) -> str:
    """Fetch the real status from YooKassa and apply it. Never trusts local state.

    Returns one of: granted | canceled | refunded | pending | mismatch | error | skip.
    """
    if not payment.yookassa_payment_id:
        return "skip"
    try:
        fetched = await yookassa.get_payment(payment.yookassa_payment_id)
    except Exception as e:
        log.warning("reconcile_fetch_failed", payment_id=payment.id, error=str(e))
        return "error"

    status = fetched.get("status")

    # A captured payment that was later refunded
    refunded = float((fetched.get("refunded_amount") or {}).get("value") or 0)
    if status == "succeeded" and refunded > 0:
        await refund_payment(session, payment, pbot)
        return "refunded"

    if status == "succeeded":
        item = get_item(payment.item_code)
        paid = float((fetched.get("amount") or {}).get("value") or 0)
        if item is None or abs(paid - float(item.amount_rub)) > 0.01:
            log.warning("reconcile_amount_mismatch", payment_id=payment.id, paid=paid)
            expected = f"{item.amount_rub}₽" if item is not None else "товар не найден"
            await _alert_ops_once(
                pbot,
                f"mismatch:{payment.id}",
                "🚨 Платёж ОПЛАЧЕН в YooKassa, но сумма/товар не совпали — "
                "начисление НЕ сделано, нужна ручная проверка (возможно, вернуть деньги).\n"
                f"payment_id={payment.id}, user_id={payment.user_id}, "
                f"item={payment.item_code}\nоплачено={paid}₽, ожидалось={expected}.",
            )
            return "mismatch"
        granted = await grant_payment(session, payment, pbot)
        # First card payment of a subscription: capture the saved-card token (now
        # exposed on the fetched payment) so the scheduler can charge renewals.
        # Skipped when recurring is disabled (no token is saved anyway).
        if (
            granted
            and item is not None
            and item.recurring
            and payment.provider == "yookassa"
            and get_settings().recurring_enabled
        ):
            pm_id = (fetched.get("payment_method") or {}).get("id")
            user = await session.get(User, payment.user_id)
            if pm_id and user is not None and user.premium_until is not None:
                await upsert_subscription(
                    session,
                    user,
                    provider="yookassa",
                    plan_code=payment.item_code,
                    period_end=user.premium_until,
                    payment_method_id=pm_id,
                )
        return "granted"

    if status == "canceled":
        if payment.status != "canceled":
            payment.status = "canceled"
            payment.cancel_reason = "yookassa"
            await session.commit()
        return "canceled"

    return "pending"
