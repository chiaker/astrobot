"""Платформо-независимый интерфейс мессенджера.

Хендлеры (`bot/handlers/*`) работают ТОЛЬКО через типы из этого модуля, а не с
aiogram/maxapi напрямую. За интерфейсом стоят два адаптера:
  - `bot/platform/telegram.py` — поверх aiogram
  - `bot/platform/max.py`      — поверх maxapi

Это позволяет держать одну кодовую базу на оба мессенджера: бизнес-логика и
диалоги общие, различается только реализация отправки/приёма событий.

Границы абстракции:
  - Отправка/редактирование/ответ на callback/медиа  → `PlatformContext`
  - Клавиатура                                        → `Keyboard` / `Button`
  - FSM (машина состояний онбординга и т.п.)          → `StateStore`
  - Отправка вне хендлера (пуши, ops-алерты)          → `PlatformBot`

Устойчивость (flood-retry, «сообщение не изменено», фолбэк при ошибке разметки)
живёт ВНУТРИ адаптеров — хендлеры про неё не знают и просто зовут reply/edit.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ─────────────────────────── Клавиатуры ───────────────────────────


@dataclass(frozen=True)
class Button:
    """Одна inline-кнопка. Ровно одно из payload/url задаёт поведение.

    payload — callback (Telegram callback_data / MAX callback payload).
    url     — кнопка-ссылка.
    """

    text: str
    payload: str | None = None
    url: str | None = None

    @property
    def is_link(self) -> bool:
        return self.url is not None


@dataclass
class Keyboard:
    """Нейтральное описание inline-клавиатуры. Адаптер рендерит в разметку SDK."""

    rows: list[list[Button]] = field(default_factory=list)

    def row(self, *buttons: Button) -> Keyboard:
        """Добавить ряд кнопок (пустые ряды игнорируются — оба SDK их не любят)."""
        cleaned = [b for b in buttons if b is not None]
        if cleaned:
            self.rows.append(cleaned)
        return self

    def add(self, buttons: list[list[Button]]) -> Keyboard:
        for r in buttons:
            self.row(*r)
        return self

    def is_empty(self) -> bool:
        return not any(self.rows)

    @classmethod
    def from_rows(cls, rows: list[list[Button]]) -> Keyboard:
        kb = cls()
        return kb.add(rows)


@dataclass(frozen=True)
class Media:
    """Источник медиа, нейтральный к платформе. Задаётся ровно один способ.

    file_id — платформо-нативный id для переиспользования уже загруженного файла
              (Telegram file_id / MAX-токен). НЕ переносится между платформами.
    """

    path: str | None = None
    url: str | None = None
    file_id: str | None = None
    data: bytes | None = None
    filename: str | None = None

    @classmethod
    def from_path(cls, path: str) -> Media:
        return cls(path=path)

    @classmethod
    def from_bytes(cls, data: bytes, filename: str = "image.png") -> Media:
        return cls(data=data, filename=filename)

    @classmethod
    def from_url(cls, url: str) -> Media:
        return cls(url=url)

    @classmethod
    def from_file_id(cls, file_id: str) -> Media:
        return cls(file_id=file_id)


@dataclass(frozen=True)
class SentMessage:
    """Результат отправки. message_id нужен для последующего edit/удаления,
    хранения в БД (User.natal_cache_message_ids, Response.message_ids)."""

    message_id: int | str


# ─────────────────────────── Входящее событие ───────────────────────────


class PlatformContext(ABC):
    """То, что хендлер получает вместо aiogram Message/CallbackQuery.

    Один объект и для текстового сообщения, и для нажатия кнопки: различаются
    через `is_callback` и `payload`.
    """

    # --- идентификация и полезная нагрузка ---

    @property
    @abstractmethod
    def user_id(self) -> int:
        """Внешний id пользователя (tg_user_id / max_user_id) → User.external_user_id."""

    @property
    @abstractmethod
    def chat_id(self) -> int:
        ...

    @property
    @abstractmethod
    def username(self) -> str | None:
        """@username без «@», либо None."""

    @property
    @abstractmethod
    def text(self) -> str | None:
        """Текст входящего сообщения (None для callback без текста)."""

    @property
    @abstractmethod
    def payload(self) -> str | None:
        """callback-данные нажатой кнопки. None, если это не callback."""

    @property
    @abstractmethod
    def is_callback(self) -> bool:
        ...

    # --- исходящие действия ---

    @abstractmethod
    async def reply(
        self,
        text: str,
        kb: Keyboard | None = None,
        *,
        disable_preview: bool = True,
    ) -> SentMessage:
        """Отправить НОВОЕ сообщение в чат. HTML-разметка включена адаптером."""

    @abstractmethod
    async def edit(
        self, text: str, kb: Keyboard | None = None, *, disable_preview: bool = True
    ) -> SentMessage:
        """Отредактировать сообщение, к которому привязана кнопка (навигация меню).
        Если редактирование невозможно (старое/без текста/не изменилось) — адаптер
        мягко откатывается на отправку нового сообщения."""

    @abstractmethod
    async def answer_callback(self, text: str | None = None, *, alert: bool = False) -> None:
        """Подтвердить callback (обязательно для обоих SDK). Для не-callback — no-op."""

    @abstractmethod
    async def send_photo(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        ...

    @abstractmethod
    async def send_animation(
        self, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        ...


# ─────────────────────────── FSM ───────────────────────────


@runtime_checkable
class StateStore(Protocol):
    """Машина состояний диалога (онбординг, оплата, вопрос…).

    Состояние — строковый ключ (напр. "onboarding:waiting_for_date"). Данные —
    произвольный dict. Aiogram FSMContext и maxapi Context оба ложатся сюда.
    """

    async def get_state(self) -> str | None: ...
    async def set_state(self, state: str | None) -> None: ...
    async def get_data(self) -> dict[str, Any]: ...
    async def update_data(self, **kwargs: Any) -> dict[str, Any]: ...
    async def clear(self) -> None: ...


# ─────────────────────────── Отправка вне хендлера ───────────────────────────


class PlatformBot(ABC):
    """Инициативная отправка без входящего события: пуши (scheduler.py),
    ops-алерты (alerts.py), рассылки (broadcast)."""

    @abstractmethod
    async def send_message(
        self, user_id: int, text: str, kb: Keyboard | None = None
    ) -> SentMessage:
        ...

    @abstractmethod
    async def send_photo(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        ...

    @abstractmethod
    async def send_animation(
        self, user_id: int, media: Media, caption: str | None = None, kb: Keyboard | None = None
    ) -> SentMessage:
        ...

    @abstractmethod
    async def close(self) -> None:
        """Закрыть HTTP-сессию SDK при остановке приложения."""
