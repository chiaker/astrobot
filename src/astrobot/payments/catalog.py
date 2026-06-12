"""Single source of truth for purchasable items: price, description, and the
grant action applied to a User. Both the bot (creating a payment) and the
webhook (granting after success) use this — so prices and effects never drift.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from astrobot.config import get_settings
from astrobot.db.models import User
from astrobot.limits import (
    NATAL_REGEN_PRICE_RUB,
    QUESTION_PACK_PRICE_RUB,
    QUESTION_PACK_SIZE,
)


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    price_rub: int
    duration_days: int
    duration_label: str
    bullets: tuple[str, ...]


PLANS: tuple[Plan, ...] = (
    Plan(
        code="month",
        title="Премиум на месяц",
        price_rub=299,
        duration_days=30,
        duration_label="30 дней",
        bullets=(
            "3 гороскопа в день (день / неделя / месяц)",
            "10 вопросов Астре в месяц",
            "Утренний гороскоп в 9:00 (опционально)",
            "Уведомления о новолунии и полнолунии",
        ),
    ),
    Plan(
        code="half",
        title="Премиум на полгода",
        price_rub=1499,
        duration_days=180,
        duration_label="180 дней",
        bullets=(
            "Всё из месячного",
            "Экономия ~17%",
        ),
    ),
    Plan(
        code="year",
        title="Премиум на год",
        price_rub=2499,
        duration_days=365,
        duration_label="365 дней",
        bullets=(
            "Всё из полугодового",
            "Экономия ~30%",
        ),
    ),
)


@dataclass(frozen=True)
class Item:
    code: str
    kind: str  # "subscription" | "natal_regen" | "question_pack"
    title: str
    amount_rub: int
    grant: Callable[[User], None]


def _grant_subscription(days: int) -> Callable[[User], None]:
    def grant(user: User) -> None:
        now = datetime.now(UTC)
        base = (
            user.premium_until
            if user.premium_until and user.premium_until > now
            else now
        )
        user.premium_until = base + timedelta(days=days)

    return grant


def _grant_natal_regen(user: User) -> None:
    user.natal_regens_bonus = (user.natal_regens_bonus or 0) + 1


def _grant_question_pack(user: User) -> None:
    user.bonus_questions = (user.bonus_questions or 0) + QUESTION_PACK_SIZE


def _build_items() -> dict[str, Item]:
    items: dict[str, Item] = {}
    for p in PLANS:
        items[p.code] = Item(
            code=p.code,
            kind="subscription",
            title=p.title,
            amount_rub=p.price_rub,
            grant=_grant_subscription(p.duration_days),
        )
    items["natal_regen"] = Item(
        code="natal_regen",
        kind="natal_regen",
        title="Пересчёт натальной карты",
        amount_rub=NATAL_REGEN_PRICE_RUB,
        grant=_grant_natal_regen,
    )
    items["question_pack"] = Item(
        code="question_pack",
        kind="question_pack",
        title=f"Пакет {QUESTION_PACK_SIZE} вопросов",
        amount_rub=QUESTION_PACK_PRICE_RUB,
        grant=_grant_question_pack,
    )
    return items


ITEMS: dict[str, Item] = _build_items()


def get_item(code: str) -> Item | None:
    return ITEMS.get(code)


def build_receipt(email: str, item: Item) -> dict:
    """54-ФЗ fiscal receipt object for YooKassa. vat_code from settings."""
    vat_code = get_settings().yookassa_vat_code
    return {
        "customer": {"email": email},
        "items": [
            {
                "description": item.title[:128],
                "quantity": "1.00",
                "amount": {"value": f"{item.amount_rub:.2f}", "currency": "RUB"},
                "vat_code": vat_code,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }
        ],
    }
