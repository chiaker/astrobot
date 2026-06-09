from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_PREMIUM
from astrobot.db.models import User
from astrobot.limits import is_premium

router = Router(name="payment")


@dataclass
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
            "До 30 вопросов в день",
            "До 5 гороскопов в день",
            "Приоритет в очереди",
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
            "Синастрия (когда добавим)",
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
            "Эксклюзивные транзитные прогнозы",
        ),
    ),
)


def _plans_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 {p.title} — {p.price_rub} ₽",
                    callback_data=f"pay:{p.code}",
                )
            ]
            for p in PLANS
        ]
    )


def _intro_text(user: User) -> str:
    if is_premium(user) and user.premium_until:
        until = user.premium_until.strftime("%d.%m.%Y")
        return (
            "💎 <b>Премиум активен</b>\n\n"
            f"Действует до <b>{until}</b>. Звёзды в твоём распоряжении ✨\n\n"
            "Можно продлить — следующий платёж сложится к текущему сроку."
        )

    lines = [
        "💎 <b>Премиум-подписка</b>",
        "",
        "Бесплатно у тебя: 1 натальная карта, 1 гороскоп в день, 3 вопроса всего.",
        "",
        "Премиум открывает Астру по-настоящему:",
    ]
    for p in PLANS:
        lines.append("")
        lines.append(f"<b>{p.title}</b> — {p.price_rub} ₽ ({p.duration_label})")
        for b in p.bullets:
            lines.append(f"• {b}")
    return "\n".join(lines)


@router.message(F.text == MENU_PREMIUM)
async def on_premium(message: Message, user: User) -> None:
    await message.answer(_intro_text(user), reply_markup=_plans_kb())


@router.callback_query(F.data.startswith("pay:"))
async def on_pay(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    code = call.data.split(":", 1)[1]
    plan = next((p for p in PLANS if p.code == code), None)
    if plan is None:
        await call.answer("План не найден", show_alert=True)
        return

    now = datetime.now(UTC)
    base = user.premium_until if user.premium_until and user.premium_until > now else now
    user.premium_until = base + timedelta(days=plan.duration_days)
    await session.commit()

    until_str = user.premium_until.strftime("%d.%m.%Y")
    await call.message.answer(
        "🧪 <b>Тестовый режим</b> — реальной оплаты не было.\n\n"
        f"Активирую <b>{plan.title}</b> до <b>{until_str}</b>. "
        f"Теперь можешь спрашивать и смотреть гороскопы без жёстких границ ✨\n\n"
        "<i>Когда подключим YooKassa/Telegram Stars — здесь будет реальная ссылка на оплату.</i>"
    )
    await call.answer("Премиум активирован")
