from __future__ import annotations

from datetime import UTC, datetime, timedelta

from astrobot.db.models import User
from astrobot.limits import (
    PREMIUM_LIMITS,
    PREMIUM_QUESTION_PERIOD_DAYS,
    check_question,
    next_premium_questions_reset,
    reset_premium_questions_if_due,
)
from astrobot.payments.catalog import get_item

PERIOD = PREMIUM_QUESTION_PERIOD_DAYS  # 30


def _premium_user(*, reset_at: datetime | None, used: int = 0) -> User:
    """A premium user (premium_until far in the future) with a given monthly
    anchor and usage. Other quota buckets empty so the premium path is exercised."""
    u = User()
    u.premium_until = datetime.now(UTC) + timedelta(days=365)
    u.questions_reset_at = reset_at
    u.premium_questions_used = used
    u.free_questions_balance = 0
    u.bonus_questions = 0
    return u


# ─── reset_premium_questions_if_due ────────────────────────────────────────────

def test_no_reset_within_period():
    anchor = datetime.now(UTC) - timedelta(days=10)
    u = _premium_user(reset_at=anchor, used=4)
    reset_premium_questions_if_due(u)
    assert u.premium_questions_used == 4          # untouched
    assert u.questions_reset_at == anchor         # anchor untouched


def test_reset_after_one_period_advances_anchor_by_exactly_one_period():
    anchor = datetime.now(UTC) - timedelta(days=PERIOD + 5)  # 1 period elapsed
    u = _premium_user(reset_at=anchor, used=5)
    reset_premium_questions_if_due(u)
    assert u.premium_questions_used == 0
    # Anchor moves by a whole period from the ORIGINAL anchor — NOT to "now".
    assert u.questions_reset_at == anchor + timedelta(days=PERIOD)


def test_reset_after_multiple_periods_advances_by_whole_periods():
    anchor = datetime.now(UTC) - timedelta(days=2 * PERIOD + 5)  # 2 periods elapsed
    u = _premium_user(reset_at=anchor, used=5)
    reset_premium_questions_if_due(u)
    assert u.premium_questions_used == 0
    assert u.questions_reset_at == anchor + timedelta(days=2 * PERIOD)


def test_non_premium_is_never_reset():
    u = User()
    u.premium_until = None
    u.questions_reset_at = datetime.now(UTC) - timedelta(days=40)
    u.premium_questions_used = 3
    reset_premium_questions_if_due(u)
    assert u.premium_questions_used == 3
    assert u.questions_reset_at is not None


def test_premium_without_anchor_initializes_it():
    u = _premium_user(reset_at=None, used=2)
    before = datetime.now(UTC)
    reset_premium_questions_if_due(u)
    assert u.questions_reset_at is not None
    assert u.questions_reset_at >= before
    # No usage reset on first init (there was no completed period to roll over).
    assert u.premium_questions_used == 2


# ─── next_premium_questions_reset (the date shown in the profile) ───────────────

def test_next_reset_is_anchor_plus_one_period_within_first_month():
    anchor = datetime.now(UTC) - timedelta(days=10)
    u = _premium_user(reset_at=anchor)
    assert next_premium_questions_reset(u) == anchor + timedelta(days=PERIOD)


def test_next_reset_is_in_the_future_even_before_lazy_persist():
    # 1 period already elapsed but reset_premium_questions_if_due hasn't run yet.
    anchor = datetime.now(UTC) - timedelta(days=PERIOD + 5)
    u = _premium_user(reset_at=anchor)
    nxt = next_premium_questions_reset(u)
    assert nxt == anchor + timedelta(days=2 * PERIOD)
    assert nxt > datetime.now(UTC)


def test_next_reset_consistent_before_and_after_persist():
    anchor = datetime.now(UTC) - timedelta(days=PERIOD + 5)
    u = _premium_user(reset_at=anchor, used=5)
    before = next_premium_questions_reset(u)
    reset_premium_questions_if_due(u)            # persists the rollover
    after = next_premium_questions_reset(u)
    assert before == after                       # same displayed date


def test_next_reset_none_for_non_premium():
    u = User()
    u.premium_until = None
    u.questions_reset_at = datetime.now(UTC)
    assert next_premium_questions_reset(u) is None


def test_next_reset_none_without_anchor():
    u = _premium_user(reset_at=None)
    assert next_premium_questions_reset(u) is None


# ─── anchor stays pinned to the FIRST purchase across renewals ─────────────────

def test_renewal_while_active_keeps_original_anchor():
    """Buy a month, then renew twice while still active (user's scenario:
    bought 10th, renewed 14th & 19th). The questions-reset anchor must stay
    pinned to the first purchase, not jump to each renewal."""
    item = get_item("month")
    u = User()
    u.premium_until = None

    item.grant(u)                                # first activation
    anchor0 = u.questions_reset_at
    assert anchor0 is not None
    assert u.premium_questions_used == 0

    u.premium_questions_used = 3                  # spend some questions
    item.grant(u)                                # renew #1 (still active)
    assert u.questions_reset_at == anchor0       # anchor unchanged
    assert u.premium_questions_used == 3         # usage NOT wiped by a renewal

    item.grant(u)                                # renew #2
    assert u.questions_reset_at == anchor0


def test_next_reset_date_steps_one_period_each_month():
    """Walk several months forward: each rollover resets usage and the displayed
    next-reset date advances by exactly one period, staying on the original day."""
    anchor0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    u = _premium_user(reset_at=anchor0, used=5)

    # Month boundaries pinned to the 10th: 09.02, 11.03, 10.04 (anchor + N*30d).
    for n in range(1, 4):
        # Simulate "n periods have elapsed since the original anchor".
        u.questions_reset_at = anchor0
        u.premium_questions_used = 5
        # Pretend it's just past the n-th boundary by moving the anchor back.
        u.questions_reset_at = datetime.now(UTC) - timedelta(days=n * PERIOD + 1)
        local_anchor = u.questions_reset_at

        reset_premium_questions_if_due(u)
        assert u.premium_questions_used == 0
        assert u.questions_reset_at == local_anchor + timedelta(days=n * PERIOD)
        # Next reset is always one period past the (advanced) anchor.
        assert next_premium_questions_reset(u) == u.questions_reset_at + timedelta(days=PERIOD)


# ─── check_question reflects the rollover WITHOUT mutating (side-effect free) ───

async def test_check_question_due_reports_full_quota_without_mutation():
    anchor = datetime.now(UTC) - timedelta(days=PERIOD + 1)
    u = _premium_user(reset_at=anchor, used=PREMIUM_LIMITS.question_per_month or 5)
    allow = await check_question(None, u)         # session unused on premium path
    assert allow.allowed is True                  # rolled-over month → quota free again
    assert allow.used == 0
    # check_question must NOT have mutated the user (reset happens in consume path).
    assert u.questions_reset_at == anchor
    assert u.premium_questions_used == (PREMIUM_LIMITS.question_per_month or 5)


async def test_check_question_within_period_counts_usage():
    anchor = datetime.now(UTC) - timedelta(days=5)
    limit = PREMIUM_LIMITS.question_per_month or 5
    u = _premium_user(reset_at=anchor, used=limit)
    allow = await check_question(None, u)
    assert allow.allowed is False                 # quota spent, month not over
    assert allow.used == limit
