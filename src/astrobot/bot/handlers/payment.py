from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from astrobot.bot.keyboards import MENU_PREMIUM

router = Router(name="payment")


@dataclass
class Plan:
    code: str
    title: str
    price_rub: int
    duration_label: str
    bullets: tuple[str, ...]


PLANS: tuple[Plan, ...] = (
    Plan(
        code="month",
        title="Премиум на месяц",
        price_rub=299,
        duration_label="30 дней",
        bullets=(
            "100 вопросов в день",
            "Безлимитные гороскопы",
            "Приоритет в очереди",
        ),
    ),
    Plan(
        code="half",
        title="Премиум на полгода",
        price_rub=1499,
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


def _intro_text() -> str:
    lines = [
        "<b>💎 Премиум-подписка</b>",
        "",
        "Сейчас у тебя <b>бесплатный</b> тариф: 20 запросов в день, базовый набор фич.",
        "",
        "Премиум открывает больше:",
    ]
    for p in PLANS:
        lines.append("")
        lines.append(f"<b>{p.title}</b> — {p.price_rub} ₽ ({p.duration_label})")
        for b in p.bullets:
            lines.append(f"• {b}")
    return "\n".join(lines)


@router.message(F.text == MENU_PREMIUM)
async def on_premium(message: Message) -> None:
    await message.answer(_intro_text(), reply_markup=_plans_kb())


@router.callback_query(F.data.startswith("pay:"))
async def on_pay(call: CallbackQuery) -> None:
    code = call.data.split(":", 1)[1]
    plan = next((p for p in PLANS if p.code == code), None)
    if plan is None:
        await call.answer("План не найден", show_alert=True)
        return

    await call.message.answer(
        f"🚧 <b>Это пока заглушка.</b>\n\n"
        f"План <b>{plan.title}</b> за <b>{plan.price_rub} ₽</b> готов к покупке, "
        f"но платёжная интеграция (YooKassa / Telegram Stars) ещё не подключена.\n\n"
        f"Когда заработает — деньги спишутся, премиум активируется автоматически."
    )
    await call.answer("Платежи скоро")
