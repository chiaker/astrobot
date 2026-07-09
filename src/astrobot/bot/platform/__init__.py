"""Слой платформы: единый интерфейс мессенджера над aiogram и maxapi.

Хендлеры импортируют нейтральные типы отсюда:

    from astrobot.bot.platform import Button, Keyboard, PlatformContext, Media

Адаптеры (`telegram`, `max`) НЕ импортируются на уровне пакета намеренно: каждый
тянет свой тяжёлый SDK, а деплой поднимает только один. Выбирайте адаптер лениво
через `load_adapter()` по значению PLATFORM.
"""
from __future__ import annotations

from importlib import import_module
from types import ModuleType

from astrobot.bot.platform.base import (
    Button,
    Keyboard,
    Media,
    PlatformBot,
    PlatformContext,
    SentMessage,
    StateStore,
)

__all__ = [
    "Button",
    "Keyboard",
    "Media",
    "PlatformBot",
    "PlatformContext",
    "SentMessage",
    "StateStore",
    "load_adapter",
]


def load_adapter(platform: str) -> ModuleType:
    """Вернуть модуль-адаптер по имени платформы ('telegram' | 'max').

    Импорт ленивый: SDK нужной платформы подтягивается только при вызове, так что
    Telegram-деплою не нужен maxapi, а MAX-деплою — aiogram сверх необходимого.
    """
    if platform not in ("telegram", "max"):
        raise ValueError(f"Неизвестная платформа: {platform!r} (ожидалось 'telegram'|'max')")
    return import_module(f"astrobot.bot.platform.{platform}")
