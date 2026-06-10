from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import LLMUsageLog, User

Tier = Literal["free", "premium"]
Kind = Literal["natal", "horoscope", "question"]


@dataclass(frozen=True)
class LimitSpec:
    natal_lifetime: int
    horoscope_per_day: int
    question_lifetime: int | None
    question_per_day: int | None
    question_per_month: int | None = field(default=None)


FREE_LIMITS = LimitSpec(
    natal_lifetime=1,
    horoscope_per_day=1,
    question_lifetime=3,
    question_per_day=None,
    question_per_month=None,
)

# Premium: 3 horoscopes/day, 10 questions/month, extra packs via bonus_questions
PREMIUM_LIMITS = LimitSpec(
    natal_lifetime=99,
    horoscope_per_day=3,
    question_lifetime=None,
    question_per_day=None,
    question_per_month=10,
)


def is_premium(user: User) -> bool:
    return user.premium_until is not None and user.premium_until > datetime.now(UTC)


def tier_of(user: User) -> Tier:
    return "premium" if is_premium(user) else "free"


def spec_of(user: User) -> LimitSpec:
    return PREMIUM_LIMITS if is_premium(user) else FREE_LIMITS


@dataclass
class Allowance:
    allowed: bool
    used: int
    limit: int
    window: Literal["day", "lifetime", "month"]
    tier: Tier


async def _count(
    session: AsyncSession, user_id: int, kind_prefix: str, hours: int | None
) -> int:
    stmt = select(func.count(LLMUsageLog.id)).where(
        LLMUsageLog.user_id == user_id,
        LLMUsageLog.kind.like(f"{kind_prefix}%"),
    )
    if hours is not None:
        since = datetime.now(UTC) - timedelta(hours=hours)
        stmt = stmt.where(LLMUsageLog.created_at >= since)
    return (await session.scalar(stmt)) or 0


async def check_natal(session: AsyncSession, user: User) -> Allowance:
    s = spec_of(user)
    used = await _count(session, user.id, "natal", hours=None)
    return Allowance(
        allowed=used < s.natal_lifetime,
        used=used,
        limit=s.natal_lifetime,
        window="lifetime",
        tier=tier_of(user),
    )


async def check_horoscope(session: AsyncSession, user: User) -> Allowance:
    s = spec_of(user)
    used = await _count(session, user.id, "horoscope", hours=24)
    return Allowance(
        allowed=used < s.horoscope_per_day,
        used=used,
        limit=s.horoscope_per_day,
        window="day",
        tier=tier_of(user),
    )


async def check_question(session: AsyncSession, user: User) -> Allowance:
    s = spec_of(user)
    t = tier_of(user)
    bonus = max(0, user.bonus_questions or 0)

    if t == "premium":
        limit = s.question_per_month or 0
        used = await _count(session, user.id, "question", hours=24 * 30)
        return Allowance(
            allowed=used < limit or bonus > 0,
            used=used,
            limit=limit + bonus,
            window="month",
            tier=t,
        )

    # free tier
    limit = s.question_lifetime or 0
    used = await _count(session, user.id, "question", hours=None)
    regular_left = max(0, limit - used)
    return Allowance(
        allowed=regular_left > 0 or bonus > 0,
        used=used,
        limit=limit + bonus,
        window="lifetime",
        tier=t,
    )


def consume_question_bonus_if_needed(user: User, used_before_call: int) -> None:
    """Decrement bonus_questions if the call exhausted the base quota."""
    bonus = user.bonus_questions or 0
    if bonus <= 0:
        return
    if is_premium(user):
        limit = PREMIUM_LIMITS.question_per_month or 0
    else:
        limit = FREE_LIMITS.question_lifetime or 0
    if used_before_call >= limit:
        user.bonus_questions = max(0, bonus - 1)


CHECKS = {
    "natal": check_natal,
    "horoscope": check_horoscope,
    "question": check_question,
}


def paywall_text(kind: Kind, allowance: Allowance) -> str:
    if allowance.tier == "premium":
        if kind == "horoscope":
            return (
                "🌙 На сегодня звёздная карта уже прочитана — вернёмся к ней завтра ✨"
            )
        # premium question monthly limit hit
        return (
            "🌙 На этот месяц <b>10 вопросов</b> израсходованы. "
            "Хочешь задать ещё — купи пакет прямо сейчас: "
            "<b>10 вопросов за 500 ₽</b> ✨"
        )
    if kind == "natal":
        return (
            "🌟 У тебя уже есть карта — она навсегда с тобой. "
            "Если хочешь пересчитать с другими данными — открой "
            "<b>👤 Профиль → Ввести данные заново</b>."
        )
    if kind == "horoscope":
        return (
            "🔮 На сегодня я уже посмотрела звёзды для тебя. "
            "Возвращайся завтра — или открой <b>💎 Премиум</b>, "
            "и сможешь спрашивать чаще ✨"
        )
    return (
        f"🌙 Ты использовал все {allowance.limit} бесплатных вопроса. "
        "Если хочешь, чтобы я отвечала без границ — открой <b>💎 Премиум</b>. "
        "Там <b>10 вопросов в месяц</b>, а если понадобится больше — "
        "пакеты по 10 вопросов за 500 ₽ ✨"
    )
