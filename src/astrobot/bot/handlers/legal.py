from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from astrobot.legal.privacy import PRIVACY_TEXT
from astrobot.legal.terms import TERMS_TEXT

router = Router(name="legal")


async def _send_chunked(target: Message, text: str) -> None:
    """Telegram caps messages at 4096 chars — split on blank lines if needed."""
    limit = 3800
    if len(text) <= limit:
        await target.answer(text, disable_web_page_preview=True)
        return
    blocks = text.split("\n\n")
    buf = ""
    for block in blocks:
        if len(buf) + len(block) + 2 > limit and buf:
            await target.answer(buf, disable_web_page_preview=True)
            buf = block
        else:
            buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        await target.answer(buf, disable_web_page_preview=True)


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    await _send_chunked(message, PRIVACY_TEXT)


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await _send_chunked(message, TERMS_TEXT)


@router.callback_query(F.data == "legal:privacy")
async def cb_privacy(call: CallbackQuery) -> None:
    await call.answer()
    await _send_chunked(call.message, PRIVACY_TEXT)


@router.callback_query(F.data == "legal:terms")
async def cb_terms(call: CallbackQuery) -> None:
    await call.answer()
    await _send_chunked(call.message, TERMS_TEXT)
