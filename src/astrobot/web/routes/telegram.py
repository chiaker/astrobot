import secrets

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request

from astrobot.config import get_settings

router = APIRouter(tags=["telegram"])


@router.post("/telegram/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    update: dict,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    settings = get_settings()
    expected = settings.webhook_secret
    # Refuse to operate without a configured secret (avoids an open webhook).
    if not expected:
        raise HTTPException(status_code=403, detail="webhook secret not configured")
    # Constant-time comparison to avoid timing side-channels.
    if not secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=403, detail="bad secret in path")
    if not secrets.compare_digest(x_telegram_bot_api_secret_token or "", expected):
        raise HTTPException(status_code=403, detail="bad secret token header")

    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp
    await dp.feed_update(bot, Update.model_validate(update))
    return {"ok": True}
