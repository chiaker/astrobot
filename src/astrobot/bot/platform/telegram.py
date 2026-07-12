"""Адаптер платформы поверх aiogram (Telegram).

Реализует интерфейс из `base.py`, инкапсулируя устойчивость, которая раньше
жила в `bot/responses.py` (flood-retry, «message is not modified», фолбэк на
плоский текст при ошибке HTML-разметки). Хендлеры получают `TelegramContext` и
не знают про aiogram.

Поведение Telegram-бота НЕ меняется — это тот же код отправки, вынесенный за
интерфейс.
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)

from astrobot.bot.formatting import strip_html
from astrobot.bot.platform.base import (
    Button,
    Keyboard,
    Media,
    PlatformBot,
    PlatformContext,
    SentMessage,
)
from astrobot.metrics import FLOOD_RETRIES_TOTAL

log = structlog.get_logger(__name__)


# ─────────────────────────── конвертеры ───────────────────────────


def _to_button(b: Button) -> InlineKeyboardButton:
    if b.is_link:
        return InlineKeyboardButton(text=b.text, url=b.url)
    # callback_data не может быть пустым — подставляем безвредный no-op.
    return InlineKeyboardButton(text=b.text, callback_data=b.payload or "noop")


def to_markup(kb: Keyboard | None) -> InlineKeyboardMarkup | None:
    if kb is None or kb.is_empty():
        return None
    rows = [[_to_button(b) for b in row] for row in kb.rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _to_input_file(media: Media) -> Any:
    """Нейтральный Media → то, что принимает aiogram (str id/url или InputFile)."""
    if media.file_id is not None:
        return media.file_id
    if media.url is not None:
        return URLInputFile(media.url)
    if media.path is not None:
        return FSInputFile(media.path)
    if media.data is not None:
        return BufferedInputFile(media.data, filename=media.filename or "file")
    raise ValueError("Media без источника (path/url/file_id/data)")


# ─────────────────────────── контекст ───────────────────────────


class TelegramContext(PlatformContext):
    """Обёртка над aiogram Message и/или CallbackQuery.

    Для message-хендлера callback=None; для callback-хендлера доступны оба
    (callback и его callback.message).
    """

    def __init__(
        self,
        *,
        bot: Bot,
        message: Message | None = None,
        callback: CallbackQuery | None = None,
    ) -> None:
        self._bot = bot
        self._callback = callback
        self._message = message or (callback.message if callback else None)
        if self._message is None:
            raise ValueError("TelegramContext требует message или callback с message")

    # --- идентификация ---

    @property
    def _from_user(self) -> Any:
        return self._callback.from_user if self._callback else self._message.from_user

    @property
    def user_id(self) -> int:
        return self._from_user.id

    @property
    def chat_id(self) -> int:
        return self._message.chat.id

    @property
    def username(self) -> str | None:
        return self._from_user.username

    @property
    def text(self) -> str | None:
        return self._message.text if not self._callback else None

    @property
    def payload(self) -> str | None:
        return self._callback.data if self._callback else None

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
        menu_fallback: bool = True,  # Telegram has a native Menu button — ignored here
    ) -> SentMessage:
        sent = await self._safe_answer(
            text,
            reply_markup=to_markup(kb),
            disable_web_page_preview=disable_preview,
        )
        return SentMessage(message_id=sent.message_id)

    async def edit(
        self,
        text: str,
        kb: Keyboard | None = None,
        *,
        disable_preview: bool = True,
        menu_fallback: bool = True,  # ignored on Telegram
    ) -> SentMessage:
        """Редактировать привязанное к кнопке сообщение; при неудаче — новое."""
        markup = to_markup(kb)
        try:
            edited = await self._message.edit_text(
                text, reply_markup=markup, disable_web_page_preview=disable_preview
            )
            # edit_text возвращает Message | True (True, если ничего не изменилось)
            mid = edited.message_id if isinstance(edited, Message) else self._message.message_id
            return SentMessage(message_id=mid)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return SentMessage(message_id=self._message.message_id)
            log.info("edit_fallback_to_send", error=str(e))
            sent = await self._safe_answer(
                text, reply_markup=markup, disable_web_page_preview=disable_preview
            )
            return SentMessage(message_id=sent.message_id)

    async def answer_callback(self, text: str | None = None, *, alert: bool = False) -> None:
        if self._callback is not None:
            await self._callback.answer(text, show_alert=alert)

    async def send_photo(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await self._message.answer_photo(
            _to_input_file(media), caption=caption, reply_markup=to_markup(kb)
        )
        return SentMessage(message_id=sent.message_id)

    async def send_animation(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await self._message.answer_animation(
            _to_input_file(media), caption=caption, reply_markup=to_markup(kb)
        )
        return SentMessage(message_id=sent.message_id)

    # --- устойчивость (перенесено из bot/responses.py:safe_answer) ---

    async def _safe_answer(self, text: str, **kwargs: Any) -> Message:
        """Отправка с двумя страховками:
        - TelegramRetryAfter: подождать и повторить один раз.
        - ошибка HTML-разметки: откат на плоский текст (теги вырезаны).
        """
        for attempt in range(2):
            try:
                return await self._message.answer(text, **kwargs)
            except TelegramRetryAfter as e:
                FLOOD_RETRIES_TOTAL.inc()
                if attempt == 1:
                    raise
                log.warning("flood_retry_after_sleep", seconds=e.retry_after)
                await asyncio.sleep(e.retry_after + 0.5)
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "can't parse entities" in msg or "unsupported start tag" in msg:
                    log.warning("html_parse_fallback", error=str(e))
                    plain = {**kwargs, "parse_mode": None}
                    return await self._message.answer(strip_html(text), **plain)
                raise
        raise RuntimeError("unreachable")


class TelegramState:
    """StateStore поверх aiogram FSMContext.

    Состояние храним строкой (aiogram сам умеет set_state(str)). Поверх — тот же
    RedisStorage, что и сейчас.
    """

    def __init__(self, fsm: FSMContext) -> None:
        self._fsm = fsm

    async def get_state(self) -> str | None:
        return await self._fsm.get_state()

    async def set_state(self, state: str | None) -> None:
        await self._fsm.set_state(state)

    async def get_data(self) -> dict[str, Any]:
        return await self._fsm.get_data()

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        return await self._fsm.update_data(**kwargs)

    async def clear(self) -> None:
        await self._fsm.clear()


# ─────────────────────────── bot-level ───────────────────────────


class TelegramBot(PlatformBot):
    """PlatformBot поверх aiogram Bot — для пушей, алертов, рассылок."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    @property
    def raw(self) -> Bot:
        """Доступ к нативному aiogram Bot (для мест, которые пока не абстрагированы)."""
        return self._bot

    async def send_message(
        self, user_id: int, text: str, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await self._bot.send_message(user_id, text, reply_markup=to_markup(kb))
        return SentMessage(message_id=sent.message_id)

    async def send_photo(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await self._bot.send_photo(
            user_id, _to_input_file(media), caption=caption, reply_markup=to_markup(kb)
        )
        return SentMessage(message_id=sent.message_id)

    async def send_animation(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        sent = await self._bot.send_animation(
            user_id, _to_input_file(media), caption=caption, reply_markup=to_markup(kb)
        )
        # Cache the id ONLY when Telegram treated it as animation/video — a document
        # fallback id would send wrong on reuse, so leave it None to re-upload.
        cacheable = sent.animation or sent.video
        return SentMessage(
            message_id=sent.message_id, file_id=cacheable.file_id if cacheable else None
        )

    async def close(self) -> None:
        await self._bot.session.close()
