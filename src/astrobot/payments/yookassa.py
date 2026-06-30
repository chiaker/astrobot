"""Async YooKassa REST client (https://api.yookassa.ru/v3).

We deliberately use httpx async instead of the official `yookassa` SDK, which is
synchronous and would block the asyncio event loop.

All calls go through `_request`, which retries transient failures (network errors
and 5xx) so a momentary blip doesn't fail a user's payment. POSTs pass a fixed
Idempotence-Key that is reused across retries, so a retry never creates a
duplicate payment or refund.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import structlog

from astrobot.config import get_settings

log = structlog.get_logger(__name__)

_API_BASE = "https://api.yookassa.ru/v3"
# 20s overall, but fail a dead connection fast (5s) so retries kick in quickly
# instead of making the user wait ~20s per attempt.
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.5)  # waits before the 2nd and 3rd attempts


class YooKassaError(RuntimeError):
    """Raised when YooKassa returns a non-success response."""


def _auth() -> tuple[str, str]:
    s = get_settings()
    return (s.yookassa_shop_id, s.yookassa_secret_key)


async def _request(
    method: str,
    path: str,
    *,
    op: str,
    headers: dict | None = None,
    json: dict | None = None,
) -> dict:
    """Call the YooKassa API with retries on transient failures.

    Retries only network errors (ConnectTimeout/ReadTimeout/ConnectError…) and 5xx
    responses. 4xx are deterministic and raised immediately. `headers` (incl. the
    caller's fixed Idempotence-Key for POSTs) is reused on every attempt, so a
    retry is idempotent and won't double-charge.
    """
    url = f"{_API_BASE}{path}"
    last_error = "unknown"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.request(
                    method, url, headers=headers, json=json, auth=_auth()
                )
            except httpx.HTTPError as e:
                last_error = type(e).__name__
                log.warning(
                    "yookassa_request_error", op=op, attempt=attempt, error=last_error
                )
            else:
                if resp.status_code < 300:
                    return resp.json()
                if resp.status_code < 500:
                    # Deterministic client error — retrying won't help.
                    log.warning(
                        "yookassa_request_failed",
                        op=op,
                        status=resp.status_code,
                        body=resp.text[:500],
                    )
                    raise YooKassaError(f"{op} HTTP {resp.status_code}")
                last_error = f"HTTP {resp.status_code}"
                log.warning(
                    "yookassa_request_5xx",
                    op=op,
                    attempt=attempt,
                    status=resp.status_code,
                    body=resp.text[:500],
                )
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(_BACKOFF_SECONDS[attempt])
    raise YooKassaError(f"{op} failed after {_MAX_ATTEMPTS} attempts ({last_error})")


async def create_payment(
    *,
    amount_rub: float,
    description: str,
    metadata: dict,
    receipt: dict | None,
    return_url: str,
    save_payment_method: bool = False,
) -> dict:
    """Create a YooKassa payment. Returns the raw payment JSON.

    Caller reads `id`, `confirmation.confirmation_url`, `status` from the result.

    save_payment_method=True asks YooKassa to tokenize the card for later
    off-session charges; after the payment succeeds the saved token id is exposed
    as `payment_method.id` and reused via create_recurring_payment.
    """
    body: dict = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": description[:128],
        "metadata": metadata,
    }
    if save_payment_method:
        body["save_payment_method"] = True
    if receipt is not None:
        body["receipt"] = receipt

    headers = {"Idempotence-Key": uuid4().hex}
    return await _request("POST", "/payments", op="create_payment", headers=headers, json=body)


async def create_recurring_payment(
    *,
    amount_rub: float,
    description: str,
    metadata: dict,
    receipt: dict | None,
    payment_method_id: str,
) -> dict:
    """Charge a saved card token off-session (subscription renewal).

    No `confirmation` block — the saved `payment_method_id` authorizes the charge
    without user interaction. Returns the raw payment JSON; `status` is usually
    `succeeded` immediately, but may be `pending` (then the webhook/reconcile job
    resolves it).
    """
    body: dict = {
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": description[:128],
        "metadata": metadata,
    }
    if receipt is not None:
        body["receipt"] = receipt

    headers = {"Idempotence-Key": uuid4().hex}
    return await _request(
        "POST", "/payments", op="create_recurring_payment", headers=headers, json=body
    )


async def get_payment(payment_id: str) -> dict:
    """Fetch a payment by id — used by the webhook to verify the real status."""
    return await _request("GET", f"/payments/{payment_id}", op="get_payment")


async def create_refund(payment_id: str, amount_rub: float) -> dict:
    """Full refund of a captured payment. Returns the refund JSON."""
    body = {
        "payment_id": payment_id,
        "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
    }
    headers = {"Idempotence-Key": uuid4().hex}
    return await _request("POST", "/refunds", op="create_refund", headers=headers, json=body)
