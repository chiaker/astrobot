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
    if secret != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad secret in path")
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="bad secret token header")

    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp
    await dp.feed_update(bot, Update.model_validate(update))
    return {"ok": True}
