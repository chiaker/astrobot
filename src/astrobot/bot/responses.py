from __future__ import annotations

import asyncio

import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html, strip_html
from astrobot.bot.keyboards import MENU_BACK_NEW_BTN, promo_row
from astrobot.db.models import Response, User
from astrobot.metrics import FLOOD_RETRIES_TOTAL

log = structlog.get_logger(__name__)

CHUNK_LIMIT = 3800
INTER_MESSAGE_DELAY = 0.08


async def edit_or_send(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    **kwargs,
) -> None:
    """Edit the callback's message in place; fall back to a fresh message if the
    original can't be edited (too old / has no text / unchanged)."""
    try:
        await call.message.edit_text(text, reply_markup=reply_markup, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return
        log.info("edit_or_send_fallback", error=str(e))
        await safe_answer(call.message, text, reply_markup=reply_markup, **kwargs)


def response_actions_kb(
    response_id: int,
    extra_row: list[InlineKeyboardButton] | None = None,
    user: User | None = None,
) -> InlineKeyboardMarkup:
    save = InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{response_id}")
    rows: list[list[InlineKeyboardButton]] = [[save]]
    if extra_row:
        rows.append(extra_row)
    if user is not None:
        rows.append(promo_row(user))
    rows.append([MENU_BACK_NEW_BTN])
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
    extra_row: list[InlineKeyboardButton] | None = None,
    user: User | None = None,
) -> list[int]:
    rendered = md_to_telegram_html(text)
    chunks = chunk_text(rendered)
    ids: list[int] = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(INTER_MESSAGE_DELAY)
        kb = (
            response_actions_kb(resp_id, extra_row, user)
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
    # Always send the detailed version; the brief/full toggle was removed.
    resp = Response(user_id=user.id, kind=kind, brief=brief, full=full)
    session.add(resp)
    await session.flush()

    resp.message_ids = await _send_chunks(message, full, resp.id, extra_row, user)
    await session.commit()
    return resp
