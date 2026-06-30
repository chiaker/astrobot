from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from astrobot.bot.handlers.broadcast import _variant_question
from astrobot.bot.keyboards import build_broadcast_kb
from astrobot.db.models import BroadcastVariant, User
from astrobot.limits import segment_of
from astrobot.scheduler import _send_broadcast_variant


def _now() -> datetime:
    return datetime.now(UTC)


# ─── segment_of: each user maps to exactly one segment ─────────────────────────

def test_segment_not_onboarded_regardless_of_balance():
    u = User(premium_until=None, free_questions_balance=2, bonus_questions=0)
    assert segment_of(u, has_profile=False) == "not_onboarded"


def test_segment_free_has_questions():
    u = User(premium_until=None, free_questions_balance=2, bonus_questions=0)
    assert segment_of(u, has_profile=True) == "free_has_questions"


def test_segment_free_used_up():
    u = User(premium_until=None, free_questions_balance=0, bonus_questions=0)
    assert segment_of(u, has_profile=True) == "free_used_up"


def test_segment_free_with_only_bonus_has_questions():
    u = User(premium_until=None, free_questions_balance=0, bonus_questions=3)
    assert segment_of(u, has_profile=True) == "free_has_questions"


def test_segment_premium_active_with_monthly_quota():
    u = User(
        premium_until=_now() + timedelta(days=30),
        free_questions_balance=0,
        bonus_questions=0,
        premium_questions_used=0,
        questions_reset_at=_now(),
    )
    assert segment_of(u, has_profile=True) == "premium_active"


def test_segment_premium_no_questions():
    u = User(
        premium_until=_now() + timedelta(days=30),
        free_questions_balance=0,
        bonus_questions=0,
        premium_questions_used=5,
        questions_reset_at=_now(),
    )
    assert segment_of(u, has_profile=True) == "premium_no_questions"


def test_segment_premium_rollover_due_counts_as_active():
    # Monthly quota exhausted but the 30-day window has elapsed → refreshed.
    u = User(
        premium_until=_now() + timedelta(days=30),
        free_questions_balance=0,
        bonus_questions=0,
        premium_questions_used=5,
        questions_reset_at=_now() - timedelta(days=31),
    )
    assert segment_of(u, has_profile=True) == "premium_active"


# ─── build_broadcast_kb ────────────────────────────────────────────────────────

def _callbacks(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_build_kb_button_types():
    v = BroadcastVariant(
        id=7,
        buttons=[
            {"type": "url", "label": "Сайт", "value": "https://x.io"},
            {"type": "ask", "label": "Спросить", "value": "про меркурий"},
            {"type": "premium", "label": "Премиум", "value": ""},
            {"type": "question_pack", "label": "Вопросы", "value": ""},
            {"type": "open_chat", "label": "Чат", "value": ""},
        ],
    )
    kb = build_broadcast_kb(v)
    flat = [b for row in kb.inline_keyboard for b in row]
    url_btn = next(b for b in flat if b.text == "Сайт")
    assert url_btn.url == "https://x.io"
    cbs = _callbacks(kb)
    assert "bcast:ask:7:1" in cbs
    assert "menu:premium" in cbs
    assert "buy:question_pack" in cbs
    assert "menu:question" in cbs
    assert "menu:open" in cbs  # trailing back-to-menu row


def test_build_kb_skips_invalid_and_returns_none_when_empty():
    assert build_broadcast_kb(BroadcastVariant(id=1, buttons=[])) is None
    # No label, or url with no value → skipped → no real buttons → None.
    v = BroadcastVariant(
        id=1,
        buttons=[
            {"type": "url", "label": "", "value": "https://x"},
            {"type": "url", "label": "Сайт", "value": ""},
        ],
    )
    assert build_broadcast_kb(v) is None


# ─── _variant_question (the ask-button handler parser) ─────────────────────────

def test_variant_question_returns_text_for_ask_button():
    v = BroadcastVariant(id=1, buttons=[{"type": "ask", "label": "A", "value": "вопрос"}])
    assert _variant_question(v, 0) == "вопрос"


def test_variant_question_none_for_non_ask_or_out_of_range():
    v = BroadcastVariant(id=1, buttons=[{"type": "premium", "label": "P", "value": ""}])
    assert _variant_question(v, 0) is None
    assert _variant_question(v, 5) is None


# ─── _send_broadcast_variant ───────────────────────────────────────────────────

async def test_send_uses_animation_when_set():
    bot = AsyncMock()
    v = BroadcastVariant(id=1, text="hi", animation="file_123", buttons=[])
    await _send_broadcast_variant(bot, 999, v)
    bot.send_animation.assert_awaited_once()
    bot.send_message.assert_not_awaited()


async def test_send_falls_back_to_text_on_bad_animation():
    bot = AsyncMock()
    bot.send_animation.side_effect = RuntimeError("bad file_id")
    v = BroadcastVariant(id=1, text="hi", animation="bad", buttons=[])
    await _send_broadcast_variant(bot, 999, v)
    bot.send_message.assert_awaited_once()


async def test_send_plain_text_when_no_animation():
    bot = AsyncMock()
    v = BroadcastVariant(id=1, text="hi", animation="", buttons=[])
    await _send_broadcast_variant(bot, 999, v)
    bot.send_animation.assert_not_awaited()
    bot.send_message.assert_awaited_once()
