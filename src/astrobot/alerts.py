from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Final

import structlog

from astrobot.config import get_settings

log = structlog.get_logger(__name__)

_ALERT_WINDOW_SEC: Final = 60.0
_ALERT_THRESHOLD: Final = 10
_NOTIFY_COOLDOWN_SEC: Final = 300.0

_error_timestamps: deque[float] = deque()
_last_notify_at: float = 0.0
_lock = asyncio.Lock()


def _bot():
    """Lazy import to avoid circular: alerts uses bot, dispatcher uses metrics."""
    from astrobot.bot.dispatcher import build_bot

    return build_bot()


async def _send(text: str) -> None:
    settings = get_settings()
    if not settings.ops_chat_id:
        log.warning("alert_skipped_no_ops_chat", text=text[:80])
        return
    bot = _bot()
    try:
        await bot.send_message(
            chat_id=settings.ops_chat_id,
            text=text,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.warning("alert_send_failed", error=str(e))
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


async def record_error(error_type: str) -> None:
    """Tally an error. If we cross the threshold in the rolling window
    (and we haven't notified recently), send an ops alert."""
    global _last_notify_at
    now = time.monotonic()
    async with _lock:
        _error_timestamps.append(now)
        while _error_timestamps and now - _error_timestamps[0] > _ALERT_WINDOW_SEC:
            _error_timestamps.popleft()

        count = len(_error_timestamps)
        if count < _ALERT_THRESHOLD:
            return
        if now - _last_notify_at < _NOTIFY_COOLDOWN_SEC:
            return
        _last_notify_at = now

    msg = (
        f"⚠️ <b>Astrobot: error spike</b>\n\n"
        f"<b>{count}</b> ошибок за последнюю минуту "
        f"(последняя: <code>{error_type}</code>).\n"
        f"Проверь логи: <code>docker compose logs --tail=200 app</code>"
    )
    await _send(msg)


async def send_critical(message: str) -> None:
    """Send an arbitrary critical message to ops (no rate-limit)."""
    await _send(f"🚨 <b>Astrobot critical</b>\n\n{message}")
