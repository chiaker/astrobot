from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import ErrorEvent, Message

from astrobot.metrics import ERRORS_TOTAL, FLOOD_RETRIES_TOTAL

router = Router(name="errors")
log = structlog.get_logger(__name__)

USER_FACING_MESSAGE = (
    "🌫 Звёзды на секунду затянуло облаком — попробуй ещё раз через минуту. "
    "Если повторяется — нажми /start, я всё помню."
)


def _extract_chat_message(event: ErrorEvent) -> Message | None:
    update = event.update
    if update.message:
        return update.message
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception

    if isinstance(exc, TelegramRetryAfter):
        FLOOD_RETRIES_TOTAL.inc()
        log.warning(
            "telegram_retry_after",
            seconds=exc.retry_after,
            method=getattr(exc, "method", None),
        )
        return True

    if isinstance(exc, TelegramBadRequest):
        msg_text = str(exc)
        if "message is not modified" in msg_text or "message to delete not found" in msg_text:
            return True
        log.warning("telegram_bad_request", error=msg_text)
        return True

    ERRORS_TOTAL.labels(error_type=type(exc).__name__).inc()
    log.exception(
        "handler_failed",
        error_type=type(exc).__name__,
        update_id=event.update.update_id,
    )

    message = _extract_chat_message(event)
    if message is not None:
        try:
            await message.answer(USER_FACING_MESSAGE)
        except Exception as send_err:
            log.warning("error_reply_failed", error=str(send_err))

    return True
