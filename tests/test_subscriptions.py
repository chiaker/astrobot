from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from astrobot.db.models import Payment, Subscription, User
from astrobot.payments import service
from astrobot.payments.catalog import get_item

# ─── recurring flag: only the 30-day plan auto-renews ──────────────────────────

def test_month_is_recurring():
    assert get_item("month").recurring is True


def test_long_plans_not_recurring():
    assert get_item("half").recurring is False
    assert get_item("year").recurring is False


def test_packs_not_recurring():
    assert get_item("question_pack").recurring is False
    assert get_item("natal_regen").recurring is False


# ─── post-purchase confirmation keyboard offers the morning horoscope ──────────

def _callbacks(kb) -> list[str]:
    if kb is None:
        return []
    return [b.payload for row in kb.rows for b in row]


def test_confirmation_kb_subscription_offers_push():
    kb = service._confirmation_kb(Payment(kind="subscription"), User(push_horoscope_enabled=False))
    assert "push:setup_start" in _callbacks(kb)


def test_confirmation_kb_no_offer_when_push_already_on():
    kb = service._confirmation_kb(Payment(kind="subscription"), User(push_horoscope_enabled=True))
    assert "push:setup_start" not in _callbacks(kb)


def test_confirmation_kb_no_offer_for_one_off_purchase():
    kb = service._confirmation_kb(Payment(kind="question_pack"), User(push_horoscope_enabled=False))
    assert "push:setup_start" not in _callbacks(kb)


# ─── upsert_subscription ───────────────────────────────────────────────────────

async def test_upsert_yookassa_arms_next_charge():
    sess = AsyncMock()
    sess.add = MagicMock()  # add() is synchronous in SQLAlchemy
    sess.scalar.return_value = None  # no existing row
    end = datetime.now(UTC) + timedelta(days=30)
    sub = await service.upsert_subscription(
        sess, User(id=1), provider="yookassa", plan_code="month",
        period_end=end, payment_method_id="pm_1",
    )
    assert sub.status == "active"
    assert sub.next_charge_at == end  # card → scheduler charges at period end
    assert sub.yookassa_payment_method_id == "pm_1"
    sess.add.assert_called_once()
    sess.commit.assert_awaited()


async def test_upsert_stars_has_no_next_charge():
    sess = AsyncMock()
    sess.add = MagicMock()
    sess.scalar.return_value = None
    end = datetime.now(UTC) + timedelta(days=30)
    sub = await service.upsert_subscription(
        sess, User(id=1), provider="telegram_stars", plan_code="month",
        period_end=end, telegram_charge_id="ch_1",
    )
    assert sub.next_charge_at is None  # Telegram drives renewals
    assert sub.telegram_charge_id == "ch_1"


async def test_upsert_reuses_and_reactivates_existing_row():
    existing = Subscription(
        user_id=1, provider="yookassa", status="canceled",
        current_period_end=datetime.now(UTC), canceled_at=datetime.now(UTC),
    )
    sess = AsyncMock()
    sess.scalar.return_value = existing
    end = datetime.now(UTC) + timedelta(days=30)
    sub = await service.upsert_subscription(
        sess, User(id=1), provider="telegram_stars", plan_code="month",
        period_end=end, telegram_charge_id="ch_2",
    )
    assert sub is existing
    assert sub.status == "active"
    assert sub.canceled_at is None
    sess.add.assert_not_called()


# ─── cancel_subscription: premium survives until period end ────────────────────

async def test_cancel_stars_cancels_on_telegram_side():
    existing = Subscription(
        user_id=1, provider="telegram_stars", status="active",
        telegram_charge_id="ch_1", current_period_end=datetime.now(UTC),
    )
    sess = AsyncMock()
    sess.scalar.return_value = existing
    bot = AsyncMock()
    sub = await service.cancel_subscription(sess, User(id=1, tg_user_id=555), bot)
    bot.edit_user_star_subscription.assert_awaited_once()
    assert sub.status == "canceled"
    assert sub.next_charge_at is None


async def test_cancel_yookassa_makes_no_telegram_call():
    existing = Subscription(
        user_id=1, provider="yookassa", status="active",
        current_period_end=datetime.now(UTC),
    )
    sess = AsyncMock()
    sess.scalar.return_value = existing
    bot = AsyncMock()
    sub = await service.cancel_subscription(sess, User(id=1, tg_user_id=5), bot)
    bot.edit_user_star_subscription.assert_not_called()
    assert sub.status == "canceled"


async def test_cancel_returns_none_without_subscription():
    sess = AsyncMock()
    sess.scalar.return_value = None
    sub = await service.cancel_subscription(sess, User(id=1, tg_user_id=5), AsyncMock())
    assert sub is None
