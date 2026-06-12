from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import select

from astrobot.config import get_settings
from astrobot.db.models import Payment
from astrobot.db.session import get_sessionmaker
from astrobot.metrics import PAYMENTS_FAILED
from astrobot.payments import service

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
        return True  # allowlist disabled; re-fetching the payment is the real guard
    ips = {p.strip() for p in allow.split(",") if p.strip()}
    return _client_ip(request) in ips


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

    # refund.succeeded carries a refund object whose payment_id points to the payment
    if event == "refund.succeeded":
        target_id = obj.get("payment_id")
    else:
        target_id = obj.get("id")

    if not target_id:
        return {"ok": True}

    if event not in {"payment.succeeded", "payment.canceled", "refund.succeeded"}:
        return {"ok": True}

    bot = getattr(request.app.state, "bot", None)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.yookassa_payment_id == target_id)
        )
        if payment is None:
            log.warning("yookassa_webhook_unknown_payment", payment_id=target_id, event=event)
            return {"ok": True}

        try:
            if event == "refund.succeeded":
                await service.refund_payment(session, payment, bot)
            else:
                # payment.succeeded / payment.canceled — re-fetch & apply real status
                await service.reconcile_payment(session, payment, bot)
        except Exception as e:
            PAYMENTS_FAILED.labels(stage="webhook").inc()
            log.warning("yookassa_webhook_apply_failed", payment_id=target_id, error=str(e))

    return {"ok": True}
