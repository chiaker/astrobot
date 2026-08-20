from __future__ import annotations

import pytest

from astrobot.bot.handlers import channel_bonus as cb
from astrobot.db.models import User


class FakeCtx:
    """Ровно то, что дёргает хендлер: user_id, answer_callback, edit."""

    def __init__(self, user_id: int = 777) -> None:
        self.user_id = user_id
        self.callbacks: list[str | None] = []
        self.edits: list[str] = []

    async def answer_callback(self, text=None, *, alert=False):
        self.callbacks.append(text)

    async def edit(self, text, kb=None):
        self.edits.append(text)


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self):
        self.commits += 1


def _user() -> User:
    u = User()
    u.id = 1
    u.bonus_questions = 0
    u.channel_bonus_at = None
    return u


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    async def ok(*_a, **_kw):
        return True

    monkeypatch.setattr(cb, "rate_limit_ok", ok)


def _patch_subscribed(monkeypatch, value: bool) -> None:
    async def check(*_a, **_kw):
        return value

    monkeypatch.setattr(cb, "_is_subscribed", check)


@pytest.mark.asyncio
async def test_claim_grants_bonus_once(monkeypatch):
    _patch_subscribed(monkeypatch, True)
    user, ctx, session = _user(), FakeCtx(), FakeSession()

    await cb.on_channel_bonus_claim(ctx, session, user, pbot=None)
    assert user.bonus_questions == cb.CHANNEL_BONUS_QUESTIONS
    assert user.channel_bonus_at is not None
    assert session.commits == 1

    # Повторное нажатие ничего не начисляет — защита это channel_bonus_at.
    # Кнопка из меню не пропадает, поэтому пользователь должен УВИДЕТЬ ответ:
    # на MAX callback-ack невидим, значит отвечаем через edit().
    await cb.on_channel_bonus_claim(ctx, session, user, pbot=None)
    assert user.bonus_questions == cb.CHANNEL_BONUS_QUESTIONS
    assert session.commits == 1
    assert "уже получен" in ctx.edits[-1]

    await cb.on_channel_bonus_show(ctx, user)
    assert "уже получен" in ctx.edits[-1]


@pytest.mark.asyncio
async def test_claim_without_subscription_grants_nothing(monkeypatch):
    _patch_subscribed(monkeypatch, False)
    user, ctx, session = _user(), FakeCtx(), FakeSession()

    await cb.on_channel_bonus_claim(ctx, session, user, pbot=None)
    assert user.bonus_questions == 0
    assert user.channel_bonus_at is None
    assert session.commits == 0
    # Пользователь видит, почему бонуса нет (на MAX ack не отображается).
    assert "не вижу твою подписку" in ctx.edits[-1]


@pytest.mark.asyncio
async def test_subscription_check_failure_is_not_a_grant(monkeypatch):
    """Падение API канала не должно раздавать бонусы (fail-closed)."""

    class Boom:
        @property
        def raw(self):
            raise RuntimeError("api down")

    user, ctx, session = _user(), FakeCtx(), FakeSession()
    await cb.on_channel_bonus_claim(ctx, session, user, pbot=Boom())
    assert user.bonus_questions == 0
    assert session.commits == 0


@pytest.mark.asyncio
async def test_broken_config_is_not_a_grant(monkeypatch):
    """Нечитаемый конфиг (нет .env в CI) — тоже «не подписан», а не 500."""

    def boom():
        raise ValueError("no settings")

    monkeypatch.setattr(cb, "get_settings", boom)
    user, ctx, session = _user(), FakeCtx(), FakeSession()
    await cb.on_channel_bonus_claim(ctx, session, user, pbot=None)
    assert user.bonus_questions == 0
    assert session.commits == 0
