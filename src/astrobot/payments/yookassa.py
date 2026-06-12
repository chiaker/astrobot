"""Async YooKassa REST client (https://api.yookassa.ru/v3).

We deliberately use httpx async instead of the official `yookassa` SDK, which is
synchronous and would block the asyncio event loop.
"""
from __future__ import annotations

from uuid import uuid4

import httpx
import structlog

from astrobot.config import get_settings

log = structlog.get_logger(__name__)

_API_BASE = "https://api.yookassa.ru/v3"
_TIMEOUT = httpx.Timeout(20.0)


class YooKassaError(RuntimeError):
    """Raised when YooKassa returns a non-success response."""


def _auth() -> tuple[str, str]:
    s = get_settings()
    return (s.yookassa_shop_id, s.yookassa_secret_key)


async def create_payment(
    *,
    amount_rub: float,
    description: str,
    metadata: dict,
    receipt: dict | None,
    return_url: str,
) -> dict:
    """Create a YooKassa payment. Returns the raw payment JSON.

    Caller reads `id`, `confirmation.confirmation_url`, `status` from the result.
    """
    body: dict = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
        "metadata": metadata,
    }
    if receipt is not None:
        body["receipt"] = receipt

    headers = {"Idempotence-Key": uuid4().hex}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_API_BASE}/payments", json=body, headers=headers, auth=_auth()
        )
    if resp.status_code >= 300:
        log.warning(
            "yookassa_create_failed",
            status=resp.status_code,
            body=resp.text[:500],
        )
        raise YooKassaError(f"create_payment HTTP {resp.status_code}")
    return resp.json()


async def get_payment(payment_id: str) -> dict:
    """Fetch a payment by id — used by the webhook to verify the real status."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{_API_BASE}/payments/{payment_id}", auth=_auth())
    if resp.status_code >= 300:
        log.warning(
            "yookassa_get_failed",
            status=resp.status_code,
            payment_id=payment_id,
            body=resp.text[:500],
        )
        raise YooKassaError(f"get_payment HTTP {resp.status_code}")
    return resp.json()


async def create_refund(payment_id: str, amount_rub: float) -> dict:
    """Full refund of a captured payment. Returns the refund JSON."""
    body = {
        "payment_id": payment_id,
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
    }
    headers = {"Idempotence-Key": uuid4().hex}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_API_BASE}/refunds", json=body, headers=headers, auth=_auth()
        )
    if resp.status_code >= 300:
        log.warning(
            "yookassa_refund_failed",
            status=resp.status_code,
            payment_id=payment_id,
            body=resp.text[:500],
        )
        raise YooKassaError(f"create_refund HTTP {resp.status_code}")
    return resp.json()
