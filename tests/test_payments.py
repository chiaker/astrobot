from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrobot.db.models import Payment, User
from astrobot.payments import service
from astrobot.payments.catalog import QUESTION_PACK_SIZE, get_item

# ─── catalog: grant / revoke are exact inverses (pure, no DB) ──────────────────

def test_grant_revoke_question_pack():
    item = get_item("question_pack")
    u = User()
    item.grant(u)
    assert u.bonus_questions == QUESTION_PACK_SIZE
    item.revoke(u)
    assert u.bonus_questions == 0


def test_grant_revoke_natal_regen():
    item = get_item("natal_regen")
    u = User()
    item.grant(u)
    assert u.natal_regens_bonus == 1
    item.revoke(u)
    assert u.natal_regens_bonus == 0


def test_revoke_never_goes_negative():
    item = get_item("question_pack")
    u = User()
    item.revoke(u)  # nothing was granted
    assert u.bonus_questions == 0


def test_grant_revoke_subscription():
    item = get_item("month")  # 30 days
    u = User()
    u.premium_until = None
    item.grant(u)
    assert u.premium_until is not None and u.premium_until > datetime.now(UTC)
    item.revoke(u)
    # granted 30 then revoked 30 → effectively expired
    assert u.premium_until is None or u.premium_until <= datetime.now(UTC)


def test_subscription_stacks_on_active():
    item = get_item("month")
    u = User()
    u.premium_until = None
    item.grant(u)
    first = u.premium_until
    item.grant(u)  # extends from current expiry, not from now
    assert u.premium_until > first


# ─── consumed_fraction ─────────────────────────────────────────────────────────

async def test_consumed_subscription_is_time_based():
    item = get_item("month")  # 30 days
    p = Payment(
        item_code="month",
        kind="subscription",
        paid_at=datetime.now(UTC) - timedelta(days=15),
    )
    frac = await service.consumed_fraction(None, p, item)  # session unused for subs
    assert 0.45 < frac < 0.55


async def test_consumed_question_pack_counts_usage():
    item = get_item("question_pack")
    p = Payment(
        item_code="question_pack",
        kind="question_pack",
        user_id=1,
        paid_at=datetime.now(UTC) - timedelta(days=1),
    )
    sess = AsyncMock()
    sess.scalar.return_value = 3  # 3 of 10 used
    frac = await service.consumed_fraction(sess, p, item)
    assert abs(frac - 0.3) < 1e-9


async def test_consumed_natal_any_use_is_full():
    item = get_item("natal_regen")
    p = Payment(
        item_code="natal_regen",
        kind="natal_regen",
        user_id=1,
        paid_at=datetime.now(UTC) - timedelta(hours=1),
    )
    sess = AsyncMock()
    sess.scalar.return_value = 1
    frac = await service.consumed_fraction(sess, p, item)
    assert frac == 1.0


# ─── refund_eligibility (window + consumption gate) ────────────────────────────

def _fake_settings(window=14, pct=25):
    return SimpleNamespace(refund_window_days=window, refund_max_consumed_pct=pct)


async def test_eligible_when_fresh_and_unused(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _fake_settings())
    p = Payment(
        item_code="month",
        kind="subscription",
        user_id=1,
        paid_at=datetime.now(UTC) - timedelta(days=1),  # ~3% consumed
    )
    ok, reason = await service.refund_eligibility(AsyncMock(), p)
    assert ok, reason


async def test_blocked_after_window(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _fake_settings())
    p = Payment(
        item_code="month",
        kind="subscription",
        user_id=1,
        paid_at=datetime.now(UTC) - timedelta(days=20),
    )
    ok, reason = await service.refund_eligibility(AsyncMock(), p)
    assert not ok
    assert "14" in reason


async def test_blocked_when_over_consumption(monkeypatch):
    monkeypatch.setattr(service, "get_settings", lambda: _fake_settings())
    # 10 of 30 days = ~33% > 25%, still inside the 14-day window
    p = Payment(
        item_code="month",
        kind="subscription",
        user_id=1,
        paid_at=datetime.now(UTC) - timedelta(days=10),
    )
    ok, reason = await service.refund_eligibility(AsyncMock(), p)
    assert not ok
    assert "%" in reason
