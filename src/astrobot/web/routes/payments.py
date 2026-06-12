from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select

from astrobot.config import get_settings
from astrobot.db.models import Payment, User
from astrobot.db.session import get_sessionmaker
from astrobot.metrics import PAYMENTS_FAILED, PAYMENTS_SUCCEEDED
from astrobot.payments import yookassa
from astrobot.payments.catalog import get_item

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _ip_allowed(request: Request) -> bool:
    allow = get_settings().yookassa_webhook_ips.strip()
    if not allow:
        return True  # allowlist disabled; re-fetch is the real guard
    ips = {p.strip() for p in allow.split(",") if p.strip()}
    return _client_ip(request) in ips


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
        return (
            "✅ Оплата прошла — вопросы зачислены. Спрашивай Астру ✨"
        )
    return "✅ Оплата прошла — спасибо ✨"


@router.post("/yookassa")
async def yookassa_webhook(request: Request) -> dict[str, bool]:
    # Always return 200 to avoid YooKassa retry storms; failures are logged.
    if not _ip_allowed(request):
        log.warning("yookassa_webhook_ip_rejected", ip=_client_ip(request))
        return {"ok": True}

    try:
        body = await request.json()
    except Exception:
        return {"ok": True}

    event = body.get("event")
    obj = body.get("object") or {}
    payment_id = obj.get("id")
    if not payment_id:
        return {"ok": True}

    if event not in {"payment.succeeded", "payment.canceled"}:
        return {"ok": True}

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.yookassa_payment_id == payment_id)
        )
        if payment is None:
            log.warning("yookassa_webhook_unknown_payment", payment_id=payment_id)
            return {"ok": True}

        if payment.status == "succeeded":
            return {"ok": True}  # idempotent: already granted

        if event == "payment.canceled":
            payment.status = "canceled"
            await session.commit()
            return {"ok": True}

        # payment.succeeded — verify the real status from the API, never trust the body
        try:
            fetched = await yookassa.get_payment(payment_id)
        except Exception as e:
            PAYMENTS_FAILED.labels(stage="webhook_fetch").inc()
            log.warning("yookassa_webhook_fetch_failed", payment_id=payment_id, error=str(e))
            return {"ok": True}

        if fetched.get("status") != "succeeded":
            log.info("yookassa_webhook_not_succeeded", payment_id=payment_id, status=fetched.get("status"))
            return {"ok": True}

        item = get_item(payment.item_code)
        if item is None:
            PAYMENTS_FAILED.labels(stage="webhook_item").inc()
            log.warning("yookassa_webhook_unknown_item", item=payment.item_code)
            return {"ok": True}

        # Guard against amount tampering
        paid_value = float((fetched.get("amount") or {}).get("value") or 0)
        if abs(paid_value - float(item.amount_rub)) > 0.01:
            PAYMENTS_FAILED.labels(stage="webhook_amount").inc()
            log.warning(
                "yookassa_webhook_amount_mismatch",
                payment_id=payment_id,
                paid=paid_value,
                expected=item.amount_rub,
            )
            return {"ok": True}

        user = await session.get(User, payment.user_id)
        if user is None:
            log.warning("yookassa_webhook_no_user", user_id=payment.user_id)
            return {"ok": True}

        item.grant(user)
        payment.status = "succeeded"
        payment.paid_at = datetime.now(UTC)
        await session.commit()
        PAYMENTS_SUCCEEDED.labels(item=payment.item_code).inc()

        # Notify the user (best-effort)
        try:
            bot = request.app.state.bot
            await bot.send_message(user.tg_user_id, _confirmation_text(payment, user))
        except Exception as e:
            log.warning("yookassa_webhook_push_failed", user_id=user.id, error=str(e))

    return {"ok": True}
