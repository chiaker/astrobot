"""Shared payment side-effects: grant on success, revoke on refund, reconcile.

Used by the webhook (web/routes/payments.py), the reconciliation job
(scheduler.py) and the admin refund action (web/routes/stats.py), so the
granting/revoking logic lives in exactly one place and is idempotent.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import Payment, User
from astrobot.metrics import PAYMENTS_REFUNDED, PAYMENTS_SUCCEEDED
from astrobot.payments import yookassa
from astrobot.payments.catalog import get_item

if TYPE_CHECKING:
    from aiogram import Bot

log = structlog.get_logger(__name__)


def _confirmation_text(payment: Payment, user: User) -> str:
    if payment.kind == "subscription":
        until = user.premium_until.strftime("%d.%m.%Y") if user.premium_until else ""
        return (
            "✅ Оплата прошла — <b>Премиум активирован</b>"
            + (f" до <b>{until}</b>" if until else "")
            + ". Звёзды теперь без ограничений ✨"
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


async def _push(bot: Bot | None, chat_id: int, text: str) -> None:
    if bot is None:
        return
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        log.warning("payment_push_failed", chat_id=chat_id, error=str(e))


async def grant_payment(session: AsyncSession, payment: Payment, bot: Bot | None) -> bool:
    """Idempotently grant the purchased item and notify. True if newly granted."""
    if payment.status == "succeeded":
        return False
    item = get_item(payment.item_code)
    user = await session.get(User, payment.user_id)
    if item is None or user is None:
        log.warning("grant_missing_item_or_user", payment_id=payment.id, item=payment.item_code)
        return False

    item.grant(user)
    payment.status = "succeeded"
    payment.paid_at = datetime.now(UTC)
    await session.commit()
    PAYMENTS_SUCCEEDED.labels(item=payment.item_code).inc()
    log.info("payment_granted", payment_id=payment.id, item=payment.item_code)

    await _push(bot, user.tg_user_id, _confirmation_text(payment, user))
    return True


async def refund_payment(session: AsyncSession, payment: Payment, bot: Bot | None) -> bool:
    """Idempotently mark a payment refunded and revoke its benefit — but only
    revoke/notify if the benefit was actually granted (status was succeeded).
    A refund of a never-granted payment just flips the status."""
    if payment.status == "refunded":
        return False
    was_granted = payment.status == "succeeded"
    item = get_item(payment.item_code)
    user = await session.get(User, payment.user_id)
    if was_granted and user is not None and item is not None:
        item.revoke(user)
    payment.status = "refunded"
    payment.refunded_at = datetime.now(UTC)
    await session.commit()
    PAYMENTS_REFUNDED.labels(item=payment.item_code).inc()
    log.info("payment_refunded", payment_id=payment.id, item=payment.item_code, revoked=was_granted)

    if was_granted and user is not None:
        await _push(bot, user.tg_user_id, _refund_text(payment))
    return True


async def reconcile_payment(session: AsyncSession, payment: Payment, bot: Bot | None) -> str:
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
        await refund_payment(session, payment, bot)
        return "refunded"

    if status == "succeeded":
        item = get_item(payment.item_code)
        paid = float((fetched.get("amount") or {}).get("value") or 0)
        if item is None or abs(paid - float(item.amount_rub)) > 0.01:
            log.warning("reconcile_amount_mismatch", payment_id=payment.id, paid=paid)
            return "mismatch"
        await grant_payment(session, payment, bot)
        return "granted"

    if status == "canceled":
        if payment.status != "canceled":
            payment.status = "canceled"
            await session.commit()
        return "canceled"

    return "pending"
