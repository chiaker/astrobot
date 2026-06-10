from __future__ import annotations

import asyncio

import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html, strip_html
from astrobot.db.models import Response, User
from astrobot.metrics import FLOOD_RETRIES_TOTAL

log = structlog.get_logger(__name__)

CHUNK_LIMIT = 3800
INTER_MESSAGE_DELAY = 0.08


def response_toggle_kb(
    response_id: int,
    current: str,
    extra_row: list[InlineKeyboardButton] | None = None,
) -> InlineKeyboardMarkup:
    if current == "brief":
        toggle = InlineKeyboardButton(
            text="📖 Подробнее", callback_data=f"resp:{response_id}:full"
        )
    else:
        toggle = InlineKeyboardButton(
            text="📝 Кратко", callback_data=f"resp:{response_id}:brief"
        )
    save = InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{response_id}")
    rows: list[list[InlineKeyboardButton]] = [[save, toggle]]
    if extra_row:
        rows.append(extra_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chunk_text(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = -1
        for sep in ("\n\n", ". ", "! ", "? ", "\n"):
            idx = remaining.rfind(sep, 0, limit)
            if idx > cut:
                cut = idx + len(sep)
        if cut <= 0 or cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


async def safe_answer(target: Message, text: str, **kwargs) -> Message:
    """Send with two safety nets:
    - TelegramRetryAfter: sleep then retry once.
    - HTML parse error: fall back to plain-text (HTML-escaped) send.
    """
    for attempt in range(2):
        try:
            return await target.answer(text, **kwargs)
        except TelegramRetryAfter as e:
            FLOOD_RETRIES_TOTAL.inc()
            if attempt == 1:
                raise
            log.warning("flood_retry_after_sleep", seconds=e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "can't parse entities" in msg or "unsupported start tag" in msg:
                log.warning("html_parse_fallback", error=str(e))
                # Strip all tags → readable plain text (html.escape shows &lt;b&gt; literally)
                kwargs_plain = {**kwargs, "parse_mode": None}
                return await target.answer(strip_html(text), **kwargs_plain)
            raise
    raise RuntimeError("unreachable")


async def _send_chunks(
    target: Message,
    text: str,
    resp_id: int,
    current: str,
    extra_row: list[InlineKeyboardButton] | None = None,
) -> list[int]:
    rendered = md_to_telegram_html(text)
    chunks = chunk_text(rendered)
    ids: list[int] = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(INTER_MESSAGE_DELAY)
        kb = (
            response_toggle_kb(resp_id, current, extra_row)
            if i == len(chunks) - 1
            else None
        )
        sent = await safe_answer(target, chunk, reply_markup=kb)
        ids.append(sent.message_id)
    return ids


async def save_and_send_response(
    message: Message,
    session: AsyncSession,
    user: User,
    kind: str,
    brief: str,
    full: str,
    extra_row: list[InlineKeyboardButton] | None = None,
) -> Response:
    resp = Response(user_id=user.id, kind=kind, brief=brief, full=full)
    session.add(resp)
    await session.flush()

    text = brief if user.default_response == "brief" else full
    resp.message_ids = await _send_chunks(
        message, text, resp.id, user.default_response, extra_row
    )
    await session.commit()
    return resp


async def replace_response(
    message: Message,
    session: AsyncSession,
    user: User,
    resp: Response,
    target: str,
) -> None:
    """Delete previous chunks of `resp`, re-render with `target` mode."""
    bot = message.bot
    for mid in list(resp.message_ids or []):
        try:
            await bot.delete_message(message.chat.id, mid)
        except Exception:
            pass

    text = resp.brief if target == "brief" else resp.full
    resp.message_ids = await _send_chunks(message, text, resp.id, target)
    await session.commit()
