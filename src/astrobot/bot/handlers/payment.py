from __future__ import annotations

import re

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.handlers.menu import send_main_menu
from astrobot.bot.keyboards import MENU_BACK_BTN
from astrobot.bot.responses import edit_or_send
from astrobot.bot.states import PaymentFlow
from astrobot.config import get_settings
from astrobot.db.models import Payment, Subscription, User
from astrobot.limits import (
    NATAL_REGEN_PRICE_RUB,
    QUESTION_PACK_30_PRICE_RUB,
    QUESTION_PACK_30_SIZE,
    QUESTION_PACK_PRICE_RUB,
    QUESTION_PACK_SIZE,
    is_premium,
)
from astrobot.metrics import PAYMENTS_CREATED, PAYMENTS_FAILED
from astrobot.payments import service, yookassa
from astrobot.payments.catalog import PLANS, Item, build_receipt, get_item
from astrobot.redis_client import get_redis

log = structlog.get_logger(__name__)
router = Router(name="payment")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Telegram Stars subscriptions support exactly one period: 30 days (2592000s).
STARS_SUBSCRIPTION_PERIOD_SEC = 2592000


def _plans_kb(active_sub: Subscription | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"💎 {p.title} ({p.duration_label}) — {p.price_rub} ₽",
                callback_data=f"buy:{p.code}",
            )
        ]
        for p in PLANS
    ]
    if active_sub is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✖ Отменить автопродление", callback_data="sub:cancel"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🔄 Пересчёт натальной карты — {NATAL_REGEN_PRICE_RUB} ₽",
                callback_data="buy:natal_regen",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"💬 Пакет {QUESTION_PACK_SIZE} вопросов — {QUESTION_PACK_PRICE_RUB} ₽",
                callback_data="buy:question_pack",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"💬 Пакет {QUESTION_PACK_30_SIZE} вопросов — {QUESTION_PACK_30_PRICE_RUB} ₽",
                callback_data="buy:question_pack_30",
            )
        ]
    )
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _method_kb(item: Item) -> InlineKeyboardMarkup:
    """Payment-method picker for a chosen item: card (YooKassa, RUB) and/or
    Telegram Stars (XTR). Card is shown only when YooKassa is configured."""
    settings = get_settings()
    rows: list[list[InlineKeyboardButton]] = []
    if settings.yookassa_shop_id and settings.yookassa_secret_key:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💳 Картой — {item.amount_rub} ₽",
                    callback_data=f"pay:{item.code}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"⭐ Telegram Stars — {item.amount_rub}",
                callback_data=f"stars:{item.code}",
            )
        ]
    )
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_PLAN_FEATURES = "\n".join(
    f"• {b}" for b in PLANS[0].bullets  # all plans share the same feature set
)

_PACKS_SECTION = (
    "<b>Разовые покупки</b> (работают и без подписки, и поверх неё):\n"
    f"• 🔄 Пересчёт натальной карты — <b>{NATAL_REGEN_PRICE_RUB} ₽</b>\n"
    f"• 💬 Пакет {QUESTION_PACK_SIZE} вопросов — <b>{QUESTION_PACK_PRICE_RUB} ₽</b>\n"
    f"• 💬 Пакет {QUESTION_PACK_30_SIZE} вопросов — <b>{QUESTION_PACK_30_PRICE_RUB} ₽</b>"
)


def _intro_text(user: User, sub: Subscription | None = None) -> str:
    if is_premium(user) and user.premium_until:
        until = user.premium_until.strftime("%d.%m.%Y")
        if sub is not None:
            renew = sub.current_period_end.strftime("%d.%m.%Y")
            sub_line = (
                f"♻️ <b>Подписка активна</b> — продлится автоматически <b>{renew}</b>.\n"
                "Отменить автопродление можно кнопкой ниже; премиум доработает "
                "оплаченный срок.\n\n"
            )
        else:
            sub_line = "Можно продлить — следующий платёж сложится к текущему сроку.\n\n"
        return (
            "💎 <b>Премиум активен</b>\n\n"
            f"Действует до <b>{until}</b>. Звёзды в твоём распоряжении ✨\n\n"
            f"Что входит:\n{_PLAN_FEATURES}\n\n"
            + sub_line
            + "— — —\n\n"
            + _PACKS_SECTION
        )

    lines = [
        "💎 <b>Премиум-подписка</b>",
        "",
        "Бесплатно: 1 натальная карта/месяц, 1 гороскоп/день, 2 вопроса по готовым темам.",
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
        _PACKS_SECTION,
    ]
    return "\n".join(lines)


async def _active_subscription(
    session: AsyncSession, user: User
) -> Subscription | None:
    return await session.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status == "active",
        )
    )


@router.callback_query(F.data == "menu:premium")
async def on_premium(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    sub = await _active_subscription(session, user)
    await edit_or_send(call, _intro_text(user, sub), _plans_kb(sub))


@router.callback_query(F.data == "premium:show")
async def on_premium_inline(
    call: CallbackQuery, session: AsyncSession, user: User
) -> None:
    sub = await _active_subscription(session, user)
    await edit_or_send(call, _intro_text(user, sub), _plans_kb(sub))
    await call.answer()


@router.callback_query(F.data == "sub:cancel")
async def on_sub_cancel(
    call: CallbackQuery, session: AsyncSession, user: User
) -> None:
    sub = await service.cancel_subscription(session, user, call.bot)
    if sub is None:
        await call.answer("Активной подписки нет", show_alert=True)
        return
    until = (
        user.premium_until.strftime("%d.%m.%Y") if user.premium_until else "конца срока"
    )
    await call.answer("Автопродление отключено")
    await edit_or_send(
        call,
        "✖ <b>Автопродление отключено.</b>\n\n"
        f"Премиум останется активным до <b>{until}</b>, после чего не продлится. "
        "Снова оформить подписку можно в любой момент.",
        _plans_kb(),
    )


@router.callback_query(F.data.startswith("buy:"))
async def on_buy(call: CallbackQuery) -> None:
    """Show the payment-method picker (card vs Telegram Stars) for an item."""
    code = call.data.split(":", 1)[1]
    item = get_item(code)
    if item is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    await call.answer()
    await edit_or_send(
        call,
        f"<b>{item.title}</b> — {item.amount_rub} ₽\n\nВыбери способ оплаты:",
        _method_kb(item),
    )


@router.callback_query(F.data.startswith("pay:"), F.data != "pay:cancel")
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
            # Recurring plan → tokenize the card so renewals can be charged
            # off-session (the token id is captured from the webhook on success).
            save_payment_method=item.recurring,
        )
    except Exception as e:
        payment.status = "canceled"
        payment.cancel_reason = "create_error"
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
            [InlineKeyboardButton(text=f"💳 Оплатить — {item.amount_rub} ₽", url=confirmation_url)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="pay:cancel")],
        ]
    )
    recurring_note = (
        f"\n\n♻️ Это <b>подписка</b>: каждые 30 дней автоматически спишется "
        f"{item.amount_rub} ₽, пока не отменишь в разделе 💎 Премиум."
        if item.recurring
        else ""
    )
    await target.answer(
        f"<b>{item.title}</b> — {item.amount_rub} ₽\n\n"
        "Нажми кнопку ниже, чтобы перейти к безопасной оплате через ЮKassa. "
        "После оплаты вернись в бот — я подтвержу начисление ✨"
        + recurring_note
        + "\n\n⚠️ <b>Обязательно отключи VPN перед оплатой</b> — иначе платёж может не пройти.\n"
        "Если что-то пошло не так — напиши нам через кнопку 🆘Поддержки в профиле.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "pay:cancel")
async def on_pay_cancel(
    call: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    pending = await session.scalar(
        select(Payment)
        .where(Payment.user_id == user.id, Payment.status == "pending")
        .order_by(desc(Payment.created_at))
        .limit(1)
    )
    if pending is not None:
        pending.status = "canceled"
        pending.cancel_reason = "user"
        await session.commit()

    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer("Оплата отменена")
    await send_main_menu(call.message, user, session)


# ─── Telegram Stars (XTR) ──────────────────────────────────────────────────────
# Native in-Telegram checkout: send_invoice → pre_checkout_query → successful_payment.
# Priced 1 star = 1 ₽, no email and no external webhook needed.

@router.callback_query(F.data.startswith("stars:"))
async def on_pay_stars(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    code = call.data.split(":", 1)[1]
    item = get_item(code)
    if item is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    await call.answer()

    # Anti-spam: one payment creation per 15s per user (shared with the card flow).
    redis = get_redis()
    try:
        allowed = await redis.set(f"pay:cd:{user.id}", "1", ex=15, nx=True)
    except Exception:
        allowed = True
    if not allowed:
        await call.message.answer(
            "⏳ Секунду — предыдущий платёж ещё оформляется. Попробуй через несколько секунд."
        )
        return

    payment = Payment(
        user_id=user.id,
        provider="telegram_stars",
        item_code=item.code,
        kind=item.kind,
        amount=item.amount_rub,
        currency="XTR",
        status="pending",
    )
    session.add(payment)
    await session.flush()

    try:
        # Recurring plan → native Telegram Stars subscription (auto-renews every
        # 30 days until canceled). Telegram sends a fresh successful_payment on
        # each renewal. Non-recurring items stay one-time invoices.
        extra = (
            {"subscription_period": STARS_SUBSCRIPTION_PERIOD_SEC}
            if item.recurring
            else {}
        )
        await call.message.bot.send_invoice(
            chat_id=call.message.chat.id,
            title=item.title[:32],
            description=f"{item.title} — Астра"[:255],
            payload=str(payment.id),
            provider_token="",  # empty for Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(label=item.title[:32], amount=item.amount_rub)],
            **extra,
        )
    except Exception as e:
        payment.status = "canceled"
        payment.cancel_reason = "create_error"
        await session.commit()
        PAYMENTS_FAILED.labels(stage="create").inc()
        log.warning("stars_invoice_failed", item=item.code, error=str(e))
        from astrobot.alerts import notify_ops

        await notify_ops(
            call.message.bot,
            f"🚨 Не удалось выставить счёт в Telegram Stars\n"
            f"item={item.code}, user_id={user.id}\nОшибка: {type(e).__name__}: {e}",
        )
        await call.message.answer(
            "🌧 Не получилось создать счёт — попробуй ещё раз чуть позже."
        )
        return

    await session.commit()
    PAYMENTS_CREATED.labels(item=item.code).inc()


@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    # Telegram requires an answer within 10s; nothing to validate further here.
    await query.answer(ok=True)


async def _payment_for_stars_renewal(
    session: AsyncSession, user: User, sp
) -> Payment | None:
    """Build a fresh Payment row for a Telegram Stars subscription renewal.

    Returns None if this renewal was already processed (Telegram can redeliver),
    keyed off the unique per-charge telegram_charge_id.
    """
    dup = await session.scalar(
        select(Payment).where(
            Payment.telegram_charge_id == sp.telegram_payment_charge_id
        )
    )
    if dup is not None:
        return None
    # Recover the plan from the original invoice payload; default to the monthly
    # subscription (the only Stars-recurring plan).
    item_code = "month"
    try:
        orig = await session.get(Payment, int(sp.invoice_payload))
    except (TypeError, ValueError):
        orig = None
    if orig is not None:
        item_code = orig.item_code
    item = get_item(item_code)
    payment = Payment(
        user_id=user.id,
        provider="telegram_stars",
        item_code=item_code,
        kind=item.kind if item else "subscription",
        amount=item.amount_rub if item else 0,
        currency="XTR",
        status="pending",
    )
    session.add(payment)
    await session.flush()
    return payment


@router.message(F.successful_payment)
async def on_successful_payment(
    message: Message,
    session: AsyncSession,
    user: User,
) -> None:
    sp = message.successful_payment
    is_renewal = bool(sp.is_recurring and not sp.is_first_recurring)

    if is_renewal:
        # Telegram auto-charged a subscription renewal. The original Payment is
        # already succeeded, so record this charge as a new Payment and grant it.
        payment = await _payment_for_stars_renewal(session, user, sp)
        if payment is None:
            return  # already processed
    else:
        try:
            payment_id = int(sp.invoice_payload)
        except (TypeError, ValueError):
            log.warning("stars_payment_bad_payload", payload=sp.invoice_payload)
            return
        payment = await session.get(Payment, payment_id)
        if payment is None or payment.user_id != user.id:
            log.warning(
                "stars_payment_unknown",
                payment_id=payment_id,
                charge=sp.telegram_payment_charge_id,
            )
            return

    payment.telegram_charge_id = sp.telegram_payment_charge_id
    await session.flush()
    # Provider-agnostic grant: idempotent, applies the benefit and notifies the user.
    granted = await service.grant_payment(session, payment, message.bot)

    # Maintain the auto-renewing subscription row for recurring (monthly) plans.
    item = get_item(payment.item_code)
    if granted and item is not None and item.recurring:
        period_end = sp.subscription_expiration_date or user.premium_until
        if period_end is not None:
            await service.upsert_subscription(
                session,
                user,
                provider="telegram_stars",
                plan_code=payment.item_code,
                period_end=period_end,
                telegram_charge_id=sp.telegram_payment_charge_id,
            )
