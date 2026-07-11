from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command

from astrobot.bot.platform import PlatformContext
from astrobot.legal.privacy import PRIVACY_TEXT
from astrobot.legal.terms import TERMS_TEXT

router = Router(name="legal")


async def _send_chunked(ctx: PlatformContext, text: str) -> None:
    """Messages cap at ~4096 chars — split on blank lines if needed."""
    limit = 3800
    if len(text) <= limit:
        await ctx.reply(text, disable_preview=True)
        return
    blocks = text.split("\n\n")
    buf = ""
    for block in blocks:
        if len(buf) + len(block) + 2 > limit and buf:
            await ctx.reply(buf, disable_preview=True)
            buf = block
        else:
            buf = f"{buf}\n\n{block}" if buf else block
    if buf:
        await ctx.reply(buf, disable_preview=True)


@router.message(Command("privacy"))
async def cmd_privacy(ctx: PlatformContext) -> None:
    await _send_chunked(ctx, PRIVACY_TEXT)


@router.message(Command("terms"))
async def cmd_terms(ctx: PlatformContext) -> None:
    await _send_chunked(ctx, TERMS_TEXT)


@router.callback_query(F.data == "legal:privacy")
async def cb_privacy(ctx: PlatformContext) -> None:
    await ctx.answer_callback()
    await _send_chunked(ctx, PRIVACY_TEXT)


@router.callback_query(F.data == "legal:terms")
async def cb_terms(ctx: PlatformContext) -> None:
    await ctx.answer_callback()
    await _send_chunked(ctx, TERMS_TEXT)
