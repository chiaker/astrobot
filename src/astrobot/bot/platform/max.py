"""Адаптер платформы поверх maxapi (мессенджер MAX).

Реализует тот же интерфейс из `base.py`, что и Telegram-адаптер, поэтому
хендлеры и бизнес-логика общие. Отличия MAX, спрятанные здесь:
  - формат разметки задаётся на КАЖДУЮ отправку (`format=TextFormat.HTML`), а не
    глобально, как `DefaultBotProperties(parse_mode=HTML)` в aiogram;
  - клавиатура строится через `InlineKeyboardBuilder`, кладётся в `attachments`;
  - медиа — `InputMedia(path=...)` / `InputMediaBuffer(buffer=..., filename=...)`.

⚠️ maxapi не установлен в dev-окружении планирования. API-вызовы взяты из
официальных примеров maxapi (examples/01,03,05,15). Точные имена полей у событий
(sender/recipient/callback) помечены `TODO(max): verify` — сверить с установленной
версией maxapi на Этапе 5. Структура при этом верна.
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from maxapi import Bot
from maxapi.enums.parse_mode import TextFormat
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.types.input_media import InputMedia, InputMediaBuffer
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from astrobot.bot.platform.base import (
    Button,
    Keyboard,
    Media,
    PlatformBot,
    PlatformContext,
    SentMessage,
)
from astrobot.metrics import CALLBACK_UNANSWERED, MAX_API_DURATION

log = structlog.get_logger(__name__)


async def _timed(op: str, coro):
    """Time one MAX API call. Splits `update_duration` (which lumps LLM + DB +
    network together) into "us" and "waiting on MAX", so a slow update points at
    a culprit instead of a range of suspects."""
    start = time.monotonic()
    try:
        return await coro
    finally:
        MAX_API_DURATION.labels(op=op).observe(time.monotonic() - start)


# ─────────────────────────── конвертеры ───────────────────────────


def _to_button(b: Button) -> Any:
    if b.is_link:
        return LinkButton(text=b.text, url=b.url)
    return CallbackButton(text=b.text, payload=b.payload or "noop")


def to_markup(kb: Keyboard | None) -> Any | None:
    """Neutral Keyboard → maxapi inline-markup (attachment) или None."""
    if kb is None or kb.is_empty():
        return None
    builder = InlineKeyboardBuilder()
    for row in kb.rows:
        if row:
            builder.row(*[_to_button(b) for b in row])
    return builder.as_markup()


def _to_input_media(media: Media) -> Any:
    """Neutral Media → maxapi InputMedia/InputMediaBuffer."""
    if media.path is not None:
        return InputMedia(path=media.path)
    if media.data is not None:
        return InputMediaBuffer(buffer=media.data, filename=media.filename or "file")
    if media.url is not None:
        # TODO(max): verify — принимает ли InputMedia параметр url в вашей версии
        # maxapi; иначе предварительно скачать и отдать через InputMediaBuffer.
        return InputMedia(url=media.url)
    if media.file_id is not None:
        # TODO(max): verify — переиспользование уже загруженного токена MAX.
        return InputMedia(token=media.file_id)
    raise ValueError("Media без источника (path/url/file_id/data)")


# MAX has no persistent "Menu" button (unlike Telegram). To keep the menu one tap
# away we attach this to any outgoing message that has no keyboard of its own.
# Payload matches the menu:open handler; inlined (not imported from bot.keyboards)
# to avoid a circular import — keyboards imports Button/Keyboard from this package.
_MENU_FALLBACK_KB = Keyboard.from_rows([[Button(text="🔙 Меню", payload="menu:open")]])


# The quietest answer MAX accepts for a callback: it refuses an empty body, but a
# whitespace notification renders as nothing the user notices. Answering at all is
# what matters — see MaxContext.finish().
_SILENT_ACK = " "


def _with_menu(kb: Keyboard | None, menu_fallback: bool) -> Keyboard | None:
    return _MENU_FALLBACK_KB if (kb is None and menu_fallback) else kb


def _attachments(kb: Keyboard | None, media: Any | None = None) -> list[Any] | None:
    """Собрать список attachments из медиа и/или клавиатуры (порядок: медиа, кнопки)."""
    items: list[Any] = []
    if media is not None:
        items.append(media)
    markup = to_markup(kb)
    if markup is not None:
        items.append(markup)
    return items or None


# ─────────────────────────── контекст ───────────────────────────


class MaxContext(PlatformContext):
    """Обёртка над событием maxapi (MessageCreated или MessageCallback)."""

    def __init__(
        self,
        *,
        bot: Bot,
        message: MessageCreated | None = None,
        callback: MessageCallback | None = None,
        suppress_menu_fallback: bool = False,
    ) -> None:
        self._bot = bot
        self._created = message
        self._callback = callback
        # True while the user is inside an FSM flow (onboarding, tarot input, …):
        # intermediate prompts shouldn't sprout a "Меню" button — tapping it would
        # abandon the flow. The menu fallback is for terminal screens only.
        self._suppress_menu = suppress_menu_fallback
        if message is None and callback is None:
            raise ValueError("MaxContext требует message или callback")
        # MAX lets a callback be "answered" exactly once (ack/answer/edit all count).
        # We defer the ack: edit() is itself the answer; a standalone ack happens in
        # finish() only if the handler changed nothing. This makes answer_callback()
        # order-independent (handlers call it before or after edit()).
        self._responded = False
        self._ack_requested = False
        self._ack_text: str | None = None

    @property
    def _event(self) -> Any:
        """Активное событие (для .message.answer / .edit / .answer)."""
        return self._callback if self._callback is not None else self._created

    # --- идентификация ---

    @property
    def user_id(self) -> int:
        if self._callback is not None:
            # TODO(max): verify — путь до отправителя callback.
            return self._callback.callback.user.user_id
        # TODO(max): verify — отправитель обычного сообщения.
        return self._created.message.sender.user_id

    @property
    def chat_id(self) -> int:
        # TODO(max): verify — recipient.chat_id присутствует в обоих событиях.
        return self._event.message.recipient.chat_id

    @property
    def _sender(self) -> Any:
        try:
            if self._callback is not None:
                return self._callback.callback.user
            return self._created.message.sender
        except AttributeError:
            return None

    @property
    def username(self) -> str | None:
        return getattr(self._sender, "username", None)

    @property
    def first_name(self) -> str | None:
        # MAX отдаёт имя то как first_name, то как name — берём что есть.
        u = self._sender
        return getattr(u, "first_name", None) or getattr(u, "name", None)

    @property
    def text(self) -> str | None:
        if self._callback is not None:
            return None
        body = getattr(self._created.message, "body", None)
        return getattr(body, "text", None) if body else None

    @property
    def payload(self) -> str | None:
        return self._callback.callback.payload if self._callback is not None else None

    @property
    def is_callback(self) -> bool:
        return self._callback is not None

    # --- исходящие действия ---

    async def reply(
        self,
        text: str,
        kb: Keyboard | None = None,
        *,
        disable_preview: bool = True,
        menu_fallback: bool = True,
    ) -> SentMessage:
        sent = await _timed(
            "send",
            self._event.message.answer(
                text,
                attachments=_attachments(
                    _with_menu(kb, menu_fallback and not self._suppress_menu)
                ),
                format=TextFormat.HTML,
            ),
        )
        return SentMessage(message_id=_message_id(sent))

    async def edit(
        self,
        text: str,
        kb: Keyboard | None = None,
        *,
        disable_preview: bool = True,
        menu_fallback: bool = True,
    ) -> SentMessage:
        # MAX не даёт per-message управления превью ссылок — disable_preview
        # принимается для совместимости интерфейса, но не применяется.
        kb = _with_menu(kb, menu_fallback and not self._suppress_menu)
        if self._callback is not None:
            # attachments=[] очищает клавиатуру, если kb=None.
            await _timed(
                "edit",
                self._callback.edit(
                    text, attachments=_attachments(kb) or [], format=TextFormat.HTML
                ),
            )
            self._responded = True  # the edit IS the callback's answer
            return SentMessage(message_id=_callback_message_id(self._callback))
        # У обычного сообщения нет edit — отправляем новое (как fallback в TG).
        return await self.reply(text, kb, menu_fallback=False)

    async def answer_callback(self, text: str | None = None, *, alert: bool = False) -> None:
        # Deferred: don't answer the callback now (that would consume the single
        # allowed response and make a following edit() a no-op). Just remember the
        # intent; finish() acks later iff nothing else responded.
        if self._callback is not None:
            self._ack_requested = True
            self._ack_text = text

    async def finish(self) -> None:
        """Answer the callback. Called by the dispatcher after each handler.

        Every callback MUST be answered. Measured on MAX: presses that follow an
        unanswered one arrive 60–180s late (astrobot_update_lag_seconds), i.e. MAX
        withholds the user's next press until the previous callback times out. That
        was the "bot reacts slowly to buttons" report.

        A handler that called edit() has already answered (edit IS the answer). One
        that only replied hasn't — and MAX rejects a contentless ack (`{}` → 400
        'message or notification required'), so the quietest answer available is a
        whitespace notification. Never let an ack failure break the handler flow."""
        if self._callback is None or self._responded:
            return
        self._responded = True
        silent = not self._ack_text
        try:
            await _timed("ack", self._callback.ack(notification=self._ack_text or _SILENT_ACK))
        except Exception as e:  # noqa: BLE001
            # Counted, not just logged: if MAX turns out to reject a whitespace
            # notification too, this counter stays >0 and the lag never drops —
            # then the answer has to come from a real edit via PUT /messages.
            CALLBACK_UNANSWERED.inc()
            log.warning("max_ack_failed", error=str(e), silent=silent)

    async def send_photo(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await _timed(
            "send_media",
            self._event.message.answer(
                caption or "",
                attachments=_attachments(kb, _to_input_media(media)),
                format=TextFormat.HTML,
            ),
        )
        return SentMessage(message_id=_message_id(sent))

    async def send_animation(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        # В MAX анимация — это то же вложение (gif/mp4), путь единый с фото.
        return await self.send_photo(media, caption, kb)


def _message_id(sent: Any) -> Any:
    """Достать id отправленного сообщения из ответа maxapi.
    TODO(max): verify — точный путь до message_id в ответе POST /messages."""
    for path in ("message.body.mid", "body.mid", "message_id", "mid"):
        obj: Any = sent
        try:
            for part in path.split("."):
                obj = getattr(obj, part)
            if obj is not None:
                return obj
        except AttributeError:
            continue
    return None


def _callback_message_id(callback: MessageCallback) -> Any:
    try:
        return callback.message.body.mid  # TODO(max): verify
    except AttributeError:
        return None


class MaxState:
    """StateStore over a maxapi context (Memory/RedisContext).

    Normalizes aiogram `State` objects (used in bot/states.py) to their string key
    so the SAME handler bodies work on both platforms: on MAX the maxapi context
    stores/filters by the identical string aiogram would (e.g. "Onboarding:...")."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def get_state(self) -> str | None:
        st = await self._ctx.get_state()
        return getattr(st, "state", st)  # normalize maxapi State → str if any

    async def set_state(self, state: Any = None) -> None:
        # Accept aiogram State | maxapi State | str | None → store the string key.
        if state is not None:
            state = getattr(state, "state", state)
        await self._ctx.set_state(state)

    async def get_data(self) -> dict[str, Any]:
        return await self._ctx.get_data()

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        return await self._ctx.update_data(**kwargs)

    async def clear(self) -> None:
        await self._ctx.clear()


# ─────────────────────────── bot-level ───────────────────────────


class MaxBot(PlatformBot):
    """PlatformBot поверх maxapi Bot — для пушей, алертов, рассылок."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    @property
    def raw(self) -> Bot:
        return self._bot

    async def send_message(
        self, user_id: int, text: str, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await _timed(
            "push",
            self._bot.send_message(
                user_id=user_id, text=text, attachments=_attachments(kb), format=TextFormat.HTML
            ),
        )
        return SentMessage(message_id=_message_id(sent))

    async def send_photo(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await _timed(
            "push_media",
            self._bot.send_message(
                user_id=user_id,
                text=caption or "",
                attachments=_attachments(kb, _to_input_media(media)),
                format=TextFormat.HTML,
            ),
        )
        return SentMessage(message_id=_message_id(sent))

    async def send_animation(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        return await self.send_photo(user_id, media, caption, kb)

    async def close(self) -> None:
        # TODO(max): verify — метод корректного закрытия сессии maxapi Bot.
        close = getattr(self._bot, "close", None) or getattr(self._bot, "session_close", None)
        if close is not None:
            await close()
