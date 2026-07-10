from __future__ import annotations

import asyncio

import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html, strip_html
from astrobot.bot.keyboards import MENU_BACK_NEW_BTN, promo_row
from astrobot.bot.platform import Button, Keyboard, PlatformContext
from astrobot.bot.platform.telegram import to_markup
from astrobot.db.models import Response, User
from astrobot.metrics import FLOOD_RETRIES_TOTAL

log = structlog.get_logger(__name__)

CHUNK_LIMIT = 3800
INTER_MESSAGE_DELAY = 0.08


def _markup(kb: Keyboard | InlineKeyboardMarkup | None) -> InlineKeyboardMarkup | None:
    """Accept a neutral Keyboard (converts) or an aiogram markup (pass-through).

    Bridge during the platform migration: handlers/keyboards now speak the
    neutral `Keyboard`, while this TG send-layer still talks to aiogram.
    """
    if isinstance(kb, Keyboard):
        return to_markup(kb)
    return kb


async def edit_or_send(
    call: CallbackQuery,
    text: str,
    reply_markup: Keyboard | InlineKeyboardMarkup | None = None,
    **kwargs,
) -> None:
    """Edit the callback's message in place; fall back to a fresh message if the
    original can't be edited (too old / has no text / unchanged)."""
    markup = _markup(reply_markup)
    try:
        await call.message.edit_text(text, reply_markup=markup, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return
        log.info("edit_or_send_fallback", error=str(e))
        await safe_answer(call.message, text, reply_markup=markup, **kwargs)


def response_actions_kb(
    response_id: int,
    extra_row: list[Button] | None = None,
    user: User | None = None,
) -> Keyboard:
    save = Button(text="⭐ Сохранить", payload=f"fav:save:{response_id}")
    rows: list[list[Button]] = [[save]]
    if extra_row:
        rows.append(extra_row)
    if user is not None and (pr := promo_row(user)):
        rows.append(pr)
    rows.append([MENU_BACK_NEW_BTN])
    return Keyboard.from_rows(rows)


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

    Accepts a neutral `Keyboard` in `reply_markup` (converted to aiogram markup).
    """
    if "reply_markup" in kwargs:
        kwargs["reply_markup"] = _markup(kwargs["reply_markup"])
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
    extra_row: list[Button] | None = None,
    user: User | None = None,
    show_actions: bool = True,
) -> list[int]:
    rendered = md_to_telegram_html(text)
    chunks = chunk_text(rendered)
    ids: list[int] = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(INTER_MESSAGE_DELAY)
        kb = (
            response_actions_kb(resp_id, extra_row, user)
            if show_actions and i == len(chunks) - 1
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
    text: str,
    extra_row: list[Button] | None = None,
    show_actions: bool = True,
) -> Response:
    # Single detailed version. brief mirrors full (column kept for favorites
    # dedup / admin preview — no separate short version is generated anymore).
    resp = Response(user_id=user.id, kind=kind, brief=text, full=text)
    session.add(resp)
    await session.flush()

    resp.message_ids = await _send_chunks(
        message, text, resp.id, extra_row, user, show_actions
    )
    await session.commit()
    return resp


# ─────────── platform-neutral (ctx) versions — used by migrated handlers ───────────
# The Message-based helpers above are the legacy Telegram path; they're removed once
# every handler is on ctx. ctx.reply already carries the flood-retry / HTML-fallback
# resilience (in the Telegram adapter), so no safe_answer needed here.


async def _send_chunks_ctx(
    ctx: PlatformContext,
    text: str,
    resp_id: int,
    extra_row: list[Button] | None = None,
    user: User | None = None,
    show_actions: bool = True,
) -> list:
    rendered = md_to_telegram_html(text)
    chunks = chunk_text(rendered)
    ids: list = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(INTER_MESSAGE_DELAY)
        kb = (
            response_actions_kb(resp_id, extra_row, user)
            if show_actions and i == len(chunks) - 1
            else None
        )
        sent = await ctx.reply(chunk, kb)
        ids.append(sent.message_id)
    return ids


async def send_response(
    ctx: PlatformContext,
    session: AsyncSession,
    user: User,
    kind: str,
    text: str,
    extra_row: list[Button] | None = None,
    show_actions: bool = True,
) -> Response:
    """ctx-based twin of save_and_send_response — saves the Response row and sends
    the (chunked, markdown-rendered) text through the platform context."""
    resp = Response(user_id=user.id, kind=kind, brief=text, full=text)
    session.add(resp)
    await session.flush()
    resp.message_ids = await _send_chunks_ctx(ctx, text, resp.id, extra_row, user, show_actions)
    await session.commit()
    return resp
