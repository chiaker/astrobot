from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import LLMUsageLog, User

Tier = Literal["free", "premium"]
Kind = Literal["natal", "horoscope", "question"]

NATAL_PER_MONTH = 1  # same for both tiers; extra via natal_regens_bonus
NATAL_REGEN_PRICE_RUB = 100
QUESTION_PACK_PRICE_RUB = 499
QUESTION_PACK_SIZE = 10
QUESTION_PACK_30_SIZE = 30
QUESTION_PACK_30_PRICE_RUB = 1299


@dataclass(frozen=True)
class LimitSpec:
    natal_per_month: int
    horoscope_per_day: int
    question_lifetime: int | None
    question_per_month: int | None = field(default=None)


FREE_LIMITS = LimitSpec(
    natal_per_month=NATAL_PER_MONTH,
    horoscope_per_day=1,
    question_lifetime=2,
    question_per_month=None,
)

# Premium: 3 horoscopes/day, 5 questions/month
# Natal: same 1/month as free (buy extra via natal_regens_bonus)
PREMIUM_LIMITS = LimitSpec(
    natal_per_month=NATAL_PER_MONTH,
    horoscope_per_day=3,
    question_lifetime=None,
    question_per_month=5,
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
    session: AsyncSession,
    user_id: int,
    kind_prefix: str,
    hours: int | None,
    not_before: datetime | None = None,
) -> int:
    stmt = select(func.count(LLMUsageLog.id)).where(
        LLMUsageLog.user_id == user_id,
        LLMUsageLog.kind.like(f"{kind_prefix}%"),
    )
    since = datetime.now(UTC) - timedelta(hours=hours) if hours is not None else None
    if not_before is not None and (since is None or not_before > since):
        since = not_before
    if since is not None:
        stmt = stmt.where(LLMUsageLog.created_at >= since)
    return (await session.scalar(stmt)) or 0


async def check_natal(session: AsyncSession, user: User) -> Allowance:
    used = await _count(session, user.id, "natal", hours=24 * 30)
    bonus = max(0, user.natal_regens_bonus or 0)
    return Allowance(
        allowed=used < NATAL_PER_MONTH or bonus > 0,
        used=used,
        limit=NATAL_PER_MONTH + bonus,
        window="month",
        tier=tier_of(user),
    )


def consume_natal_bonus_if_needed(user: User, used_before_call: int) -> None:
    """If the call used a purchased regen (not the monthly quota), decrement it."""
    if used_before_call >= NATAL_PER_MONTH and (user.natal_regens_bonus or 0) > 0:
        user.natal_regens_bonus = max(0, user.natal_regens_bonus - 1)


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
    t = tier_of(user)
    free_bal = user.free_questions_balance or 0
    bonus = max(0, user.bonus_questions or 0)

    if t == "premium":
        limit = PREMIUM_LIMITS.question_per_month or 0
        monthly_used = await _count(
            session, user.id, "question", hours=24 * 30, not_before=user.questions_reset_at
        )
        return Allowance(
            allowed=free_bal > 0 or bonus > 0 or monthly_used < limit,
            used=monthly_used,
            limit=limit,
            window="month",
            tier=t,
        )

    return Allowance(
        allowed=free_bal > 0 or bonus > 0,
        used=max(0, (FREE_LIMITS.question_lifetime or 0) - free_bal),
        limit=FREE_LIMITS.question_lifetime or 0,
        window="lifetime",
        tier=t,
    )


def consume_question_from_priority_bucket(user: User) -> None:
    """Consume from free balance first, then bonus pack, then monthly (implicit via log)."""
    if (user.free_questions_balance or 0) > 0:
        user.free_questions_balance -= 1
    elif (user.bonus_questions or 0) > 0:
        user.bonus_questions -= 1


CHECKS = {
    "natal": check_natal,
    "horoscope": check_horoscope,
    "question": check_question,
}


def paywall_text(kind: Kind, allowance: Allowance) -> str:
    if kind == "natal":
        return (
            f"🌟 Натальная карта на этот месяц уже была рассчитана.\n\n"
            f"Новая бесплатная генерация — в следующем месяце. "
            f"Или купи пересчёт прямо сейчас за <b>{NATAL_REGEN_PRICE_RUB} ₽</b> ✨"
        )
    if kind == "horoscope":
        if allowance.tier == "premium":
            return "🌙 На сегодня звёздная карта прочитана — вернёмся завтра ✨"
        return (
            "🔮 На сегодня гороскоп уже готов. "
            "Возвращайся завтра — или открой <b>💎 Премиум</b>, "
            "там 3 гороскопа в день ✨"
        )
    # question
    if allowance.tier == "premium":
        return (
            f"🌙 На этот месяц <b>{PREMIUM_LIMITS.question_per_month} вопросов</b> израсходованы. "
            f"Купи пакет — <b>{QUESTION_PACK_SIZE} вопросов за {QUESTION_PACK_PRICE_RUB} ₽</b> "
            f"или <b>{QUESTION_PACK_30_SIZE} за {QUESTION_PACK_30_PRICE_RUB} ₽</b> ✨"
        )
    return (
        f"🌙 Ты использовал все {allowance.limit} бесплатных вопроса. "
        f"Открой <b>💎 Премиум</b> — там {PREMIUM_LIMITS.question_per_month} вопросов в месяц, "
        f"или пакеты по {QUESTION_PACK_SIZE} вопросов за {QUESTION_PACK_PRICE_RUB} ₽ ✨"
    )


def free_questions_remaining(user: User) -> int:
    return user.free_questions_balance or 0
