from __future__ import annotations

import asyncio

import structlog
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html
from astrobot.db.models import Response, User
from astrobot.metrics import FLOOD_RETRIES_TOTAL

log = structlog.get_logger(__name__)

CHUNK_LIMIT = 3800
INTER_MESSAGE_DELAY = 0.08


def response_toggle_kb(response_id: int, current: str) -> InlineKeyboardMarkup:
    if current == "brief":
        button = InlineKeyboardButton(
            text="📖 Подробнее", callback_data=f"resp:{response_id}:full"
        )
    else:
        button = InlineKeyboardButton(
            text="📝 Кратко", callback_data=f"resp:{response_id}:brief"
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


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
    """Send with flood-control retry: on TelegramRetryAfter, sleep then retry once."""
    for attempt in range(2):
        try:
            return await target.answer(text, **kwargs)
        except TelegramRetryAfter as e:
            FLOOD_RETRIES_TOTAL.inc()
            if attempt == 1:
                raise
            log.warning("flood_retry_after_sleep", seconds=e.retry_after)
            await asyncio.sleep(e.retry_after + 0.5)
    raise RuntimeError("unreachable")


async def _send_chunks(
    target: Message,
    text: str,
    resp_id: int,
    current: str,
) -> list[int]:
    rendered = md_to_telegram_html(text)
    chunks = chunk_text(rendered)
    ids: list[int] = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(INTER_MESSAGE_DELAY)
        kb = response_toggle_kb(resp_id, current) if i == len(chunks) - 1 else None
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
) -> Response:
    resp = Response(user_id=user.id, kind=kind, brief=brief, full=full)
    session.add(resp)
    await session.flush()

    text = brief if user.default_response == "brief" else full
    resp.message_ids = await _send_chunks(message, text, resp.id, user.default_response)
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
