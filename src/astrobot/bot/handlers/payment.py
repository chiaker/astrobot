from __future__ import annotations

import re

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_BACK_BTN
from astrobot.bot.responses import edit_or_send
from astrobot.bot.states import PaymentFlow
from astrobot.config import get_settings
from astrobot.db.models import Payment, User
from astrobot.limits import (
    NATAL_REGEN_PRICE_RUB,
    QUESTION_PACK_PRICE_RUB,
    QUESTION_PACK_SIZE,
    is_premium,
)
from astrobot.metrics import PAYMENTS_CREATED, PAYMENTS_FAILED
from astrobot.payments import yookassa
from astrobot.payments.catalog import PLANS, build_receipt, get_item
from astrobot.redis_client import get_redis

log = structlog.get_logger(__name__)
router = Router(name="payment")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _intro_text(user: User) -> str:
    if is_premium(user) and user.premium_until:
        until = user.premium_until.strftime("%d.%m.%Y")
        return (
            "💎 <b>Премиум активен</b>\n\n"
            f"Действует до <b>{until}</b>. Звёзды в твоём распоряжении ✨\n\n"
            "Можно продлить — следующий платёж сложится к текущему сроку.\n\n"
            "— — —\n\n"
            f"💬 <b>Пакет {QUESTION_PACK_SIZE} вопросов — {QUESTION_PACK_PRICE_RUB} ₽</b>\n"
            "<i>Это отдельная разовая услуга, не входит в подписку. Бери, если "
            "не хватает 10 вопросов премиума в этом месяце — пакет добавится сверх них.</i>"
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
        "<b>Разовые покупки</b> (отдельно от подписки):",
        f"• 🔄 Пересчёт натальной карты — <b>{NATAL_REGEN_PRICE_RUB} ₽</b>",
        f"• 💬 Пакет {QUESTION_PACK_SIZE} вопросов — <b>{QUESTION_PACK_PRICE_RUB} ₽</b>",
        "<i>Пакет вопросов — доп. услуга для тех, кому не хватает лимита "
        "(и на премиуме, и без него). Это не замена подписки.</i>",
    ]
    return "\n".join(lines)


@router.callback_query(F.data == "menu:premium")
async def on_premium(call: CallbackQuery, user: User) -> None:
    await call.answer()
    await edit_or_send(call, _intro_text(user), _plans_kb())


@router.callback_query(F.data == "premium:show")
async def on_premium_inline(call: CallbackQuery, user: User) -> None:
    await edit_or_send(call, _intro_text(user), _plans_kb())
    await call.answer()


@router.callback_query(F.data.startswith("pay:"))
async def on_pay(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    code = call.data.split(":", 1)[1]
    item = get_item(code)
    if item is None:
        await call.answer("Товар не найден", show_alert=True)
        return

    await call.answer()

    # Need an email for the 54-ФЗ receipt — ask once, then reuse.
    if not user.email:
        await state.set_state(PaymentFlow.waiting_for_email)
        await state.update_data(pay_code=code)
        await call.message.answer(
            "📧 Для чека об оплате нужен <b>email</b> — отправь его одним сообщением.\n\n"
            "<i>На него придёт чек.</i>"
        )
        return

    await _start_payment(call.message, session, user, code)


@router.message(PaymentFlow.waiting_for_email)
async def on_payment_email(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    email = (message.text or "").strip()
    if not _EMAIL_RE.fullmatch(email) or len(email) > 255:
        await message.answer(
            "Хм, это не похоже на email. Пришли в формате <code>name@example.com</code>."
        )
        return

    data = await state.get_data()
    code = data.get("pay_code")
    await state.clear()

    user.email = email
    await session.commit()

    if not code or get_item(code) is None:
        await message.answer("Что-то сбилось — открой <b>💎 Премиум</b> и выбери ещё раз.")
        return

    await _start_payment(message, session, user, code)


async def _start_payment(
    target: Message,
    session: AsyncSession,
    user: User,
    code: str,
) -> None:
    item = get_item(code)
    if item is None:
        await target.answer("Товар не найден.")
        return

    settings = get_settings()
    if not settings.yookassa_shop_id or not settings.yookassa_secret_key:
        await target.answer(
            "⚙️ Оплата пока не настроена. Загляни позже — звёзды уже на подходе ✨"
        )
        return

    # Anti-spam: one payment creation per 15s per user (Redis cooldown).
    redis = get_redis()
    try:
        allowed = await redis.set(f"pay:cd:{user.id}", "1", ex=15, nx=True)
    except Exception:
        allowed = True  # Redis down → don't block real purchases
    if not allowed:
        await target.answer(
            "⏳ Секунду — предыдущий платёж ещё оформляется. Попробуй через несколько секунд."
        )
        return

    payment = Payment(
        user_id=user.id,
        provider="yookassa",
        item_code=item.code,
        kind=item.kind,
        amount=item.amount_rub,
        currency="RUB",
        status="pending",
        email=user.email,
    )
    session.add(payment)
    await session.flush()

    try:
        resp = await yookassa.create_payment(
            amount_rub=item.amount_rub,
            description=f"{item.title} — Астра",
            metadata={
                "payment_id": str(payment.id),
                "tg_user_id": str(user.tg_user_id),
                "item_code": item.code,
            },
            receipt=build_receipt(user.email or "", item),
            return_url=settings.yookassa_return_url_effective,
        )
    except Exception as e:
        payment.status = "canceled"
        await session.commit()
        PAYMENTS_FAILED.labels(stage="create").inc()
        log.warning("payment_create_failed", item=item.code, error=str(e))
        from astrobot.alerts import notify_ops

        await notify_ops(
            target.bot,
            f"🚨 Не удалось создать платёж в YooKassa\n"
            f"item={item.code}, user_id={user.id}\nОшибка: {type(e).__name__}: {e}",
        )
        await target.answer(
            "🌧 Не получилось создать платёж — попробуй ещё раз чуть позже."
        )
        return

    payment.yookassa_payment_id = resp.get("id")
    confirmation_url = (resp.get("confirmation") or {}).get("confirmation_url")
    payment.metadata_json = {"confirmation_url": confirmation_url}
    await session.commit()

    if not confirmation_url:
        PAYMENTS_FAILED.labels(stage="create").inc()
        log.warning("payment_no_confirmation_url", item=item.code, resp=str(resp)[:300])
        await target.answer(
            "🌧 Не получилось создать платёж — попробуй ещё раз чуть позже."
        )
        return

    PAYMENTS_CREATED.labels(item=item.code).inc()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить — {item.amount_rub} ₽", url=confirmation_url)]
        ]
    )
    await target.answer(
        f"<b>{item.title}</b> — {item.amount_rub} ₽\n\n"
        "Нажми кнопку ниже, чтобы перейти к безопасной оплате через ЮKassa. "
        "После оплаты вернись в бот — я подтвержу начисление ✨",
        reply_markup=kb,
    )
