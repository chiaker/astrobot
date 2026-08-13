"""Акция «подпишись на канал → +1 вопрос».

Начисляет в тот же `bonus_questions`, что и рефералка — новых лимитов не заводим.
Один раз на аккаунт: `user.channel_bonus_at` и есть защита от повтора."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog
from aiogram import F, Router
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_BACK_BTN, MENU_QUESTION, with_back
from astrobot.bot.platform import Button, Keyboard, PlatformBot, PlatformContext
from astrobot.bot.utils import rate_limit_ok
from astrobot.config import get_settings
from astrobot.db.models import User
from astrobot.metrics import CHANNEL_BONUS_CLAIMED

log = structlog.get_logger(__name__)
router = Router(name="channel_bonus")

# Меньше реферального бонуса (там +2): подписка на канал стоит человеку одного клика.
CHANNEL_BONUS_QUESTIONS = 1

# Активные статусы Telegram getChatMember. «left»/«kicked» — не подписан.
_SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}

CLAIMS_PER_HOUR = 10


async def _is_subscribed(pbot: PlatformBot, user_id: int) -> bool:
    """Проверить подписку на канал. Любая ошибка → False (бонус не выдаём).

    Требует, чтобы бот был админом канала: без этого API не отдаёт участников."""
    try:
        # get_settings() внутри try намеренно: кривой/пустой конфиг тоже должен
        # означать «не подписан», а не 500 в хендлере.
        s = get_settings()
        if s.platform == "max":
            return await _is_subscribed_max(pbot, user_id)
        member = await pbot.raw.get_chat_member(s.promo_channel_id, user_id)
        return member.status in _SUBSCRIBED_STATUSES
    except Exception as e:
        log.warning("channel_bonus_check_failed", user_id=user_id, error=str(e))
        return False


async def _is_subscribed_max(pbot: PlatformBot, user_id: int) -> bool:
    """MAX Bot API: GET /chats/{chatId}/members?user_ids=... Пустой list — не подписан.

    Прямой httpx вместо обёртки maxapi: метод есть не во всех версиях библиотеки,
    а эндпоинт стабильный."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"https://botapi.max.ru/chats/{s.promo_channel_id}/members",
            params={"user_ids": user_id},
            # Токен в query MAX больше не принимает: только заголовок, без Bearer.
            headers={"Authorization": s.bot_token},
        )
        r.raise_for_status()
        return bool(r.json().get("members"))


def _promo_text() -> str:
    return (
        "🎁 <b>+1 вопрос за подписку</b>\n\n"
        "Подпишись на наш канал — и я добавлю тебе <b>бесплатный вопрос</b> ✨\n\n"
        "1. Открой канал и подпишись\n"
        "2. Вернись сюда и нажми «Я подписался»"
    )


@router.callback_query(F.data == "chbonus:show")
async def on_channel_bonus_show(ctx: PlatformContext, user: User) -> None:
    s = get_settings()
    await ctx.answer_callback()
    if user.channel_bonus_at is not None:
        await ctx.edit("🎁 Бонус за подписку уже получен ✨", with_back([]))
        return
    kb = Keyboard.from_rows(
        [
            [Button(text="📣 Открыть канал", url=s.promo_channel_url)],
            [Button(text="✅ Я подписался", payload="chbonus:claim")],
            [MENU_BACK_BTN],
        ]
    )
    await ctx.edit(_promo_text(), kb)


@router.callback_query(F.data == "chbonus:claim")
async def on_channel_bonus_claim(
    ctx: PlatformContext, session: AsyncSession, user: User, pbot: PlatformBot
) -> None:
    if user.channel_bonus_at is not None:
        await ctx.answer_callback("Бонус уже получен ✨")
        return

    if not await rate_limit_ok(f"chbonus:{user.id}", CLAIMS_PER_HOUR, 3600):
        await ctx.answer_callback("Слишком часто. Попробуй через несколько минут.", alert=True)
        return

    if not await _is_subscribed(pbot, ctx.user_id):
        await ctx.answer_callback(
            "Пока не вижу твою подписку. Подпишись на канал и нажми ещё раз.", alert=True
        )
        return

    user.bonus_questions = (user.bonus_questions or 0) + CHANNEL_BONUS_QUESTIONS
    user.channel_bonus_at = datetime.now(UTC)
    await session.commit()
    CHANNEL_BONUS_CLAIMED.inc()

    await ctx.answer_callback("Готово ✨")
    await ctx.edit(
        "🎁 Спасибо! Я добавила тебе <b>+1 бесплатный вопрос</b> ✨\n\n"
        "Спрашивай — я слушаю.",
        with_back([[Button(text=MENU_QUESTION, payload="menu:question")]]),
    )
