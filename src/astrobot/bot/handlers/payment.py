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
from astrobot.limits import (
    NATAL_REGEN_PRICE_RUB,
    QUESTION_PACK_PRICE_RUB,
    QUESTION_PACK_SIZE,
    is_premium,
)

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


def _plans_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"💳 {p.title} — {p.price_rub} ₽",
                callback_data=f"pay:{p.code}",
            )
        ]
        for p in PLANS
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🔄 Пересчёт натальной карты — {NATAL_REGEN_PRICE_RUB} ₽",
                callback_data="pay:natal_regen",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"💬 Пакет {QUESTION_PACK_SIZE} вопросов — {QUESTION_PACK_PRICE_RUB} ₽",
                callback_data="pay:question_pack",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        "Бесплатно: 1 натальная карта/месяц, 1 гороскоп/день, 3 вопроса.",
        "",
        "Премиум открывает Астру по-настоящему:",
    ]
    for p in PLANS:
        lines.append("")
        lines.append(f"<b>{p.title}</b> — {p.price_rub} ₽ ({p.duration_label})")
        for b in p.bullets:
            lines.append(f"• {b}")
    lines += [
        "",
        "— — —",
        "",
        "<b>Разовые покупки</b> (без подписки):",
        f"• 🔄 Пересчёт натальной карты — <b>{NATAL_REGEN_PRICE_RUB} ₽</b>",
        f"• 💬 Пакет {QUESTION_PACK_SIZE} вопросов — <b>{QUESTION_PACK_PRICE_RUB} ₽</b>",
    ]
    return "\n".join(lines)


@router.message(F.text == MENU_PREMIUM)
async def on_premium(message: Message, user: User) -> None:
    await message.answer(_intro_text(user), reply_markup=_plans_kb())


@router.callback_query(F.data == "premium:show")
async def on_premium_inline(call: CallbackQuery, user: User) -> None:
    await call.message.answer(_intro_text(user), reply_markup=_plans_kb())
    await call.answer()


@router.callback_query(F.data.startswith("pay:"))
async def on_pay(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    code = call.data.split(":", 1)[1]

    # One-time purchases
    if code == "natal_regen":
        user.natal_regens_bonus = (user.natal_regens_bonus or 0) + 1
        await session.commit()
        await call.message.answer(
            "🧪 <b>Тестовый режим</b> — реальной оплаты не было.\n\n"
            "🔄 Добавила <b>1 пересчёт натальной карты</b>. "
            "Теперь можешь нажать «🌟 Натальная карта» и получить новый расчёт ✨"
        )
        await call.answer("Пересчёт добавлен")
        return

    if code == "question_pack":
        user.bonus_questions = (user.bonus_questions or 0) + QUESTION_PACK_SIZE
        await session.commit()
        await call.message.answer(
            "🧪 <b>Тестовый режим</b> — реальной оплаты не было.\n\n"
            f"💬 Добавила <b>{QUESTION_PACK_SIZE} вопросов</b>. "
            "Задавай — я отвечу ✨"
        )
        await call.answer(f"+{QUESTION_PACK_SIZE} вопросов")
        return

    # Subscription plans
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
        "Звёзды теперь без ограничений ✨\n\n"
        "<i>Когда подключим YooKassa/Telegram Stars — здесь будет реальная оплата.</i>"
    )
    await call.answer("Премиум активирован")
