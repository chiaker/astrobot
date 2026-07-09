"""Прототип переноса astrobot на MAX (библиотека maxapi).

Цель — проверить, как API maxapi ложится на существующие aiogram-хендлеры,
ПЕРЕД тем как переносить весь бот. Здесь воспроизведён тонкий срез:

  - главное меню с inline callback-кнопками  (аналог bot/keyboards.py)
  - обработка нажатий callback                (аналог bot/handlers/menu.py)
  - фича «Таро» с реальной бизнес-логикой     (импортируем ваш astrobot.tarot!)
  - отправка картинки                          (аналог отправки натальной карты)

ГЛАВНЫЙ ВЫВОД, который проверяем: файл astrobot/tarot.py переносится
БЕЗ ЕДИНОГО ИЗМЕНЕНИЯ — вся «начинка» (astrology/, llm/, tarot, lunar, db/)
не знает про мессенджер. Меняется только слой bot/.

Запуск:
    pip install maxapi
    MAX_BOT_TOKEN=<токен_бота_из_MAX> python prototype_max/bot.py

Токен бота берётся в MAX через @MasterBot (аналог BotFather).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from pathlib import Path

# Подключаем существующий пакет astrobot из ./src — БЕЗ копирования логики.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

with contextlib.suppress(ImportError):
    from dotenv import load_dotenv

    load_dotenv()

from maxapi import Bot, Dispatcher, F
from maxapi.enums.parse_mode import TextFormat
from maxapi.filters.command import CommandStart
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.input_media import InputMedia
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# ⬇⬇⬇ Ваша реальная бизнес-логика, импортируется КАК ЕСТЬ ⬇⬇⬇
from astrobot.tarot import cards_to_markdown, draw_three

logging.basicConfig(level=logging.INFO)

bot = Bot()  # токен из переменной окружения MAX_BOT_TOKEN
dp = Dispatcher()


# ───────────────────────── клавиатуры ─────────────────────────
# Сравните с bot/keyboards.py:main_menu_inline().
#
# aiogram:
#   InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
#       text="🃏 Таро", callback_data="menu:tarot")]])
#
# maxapi:
#   builder.row(CallbackButton(text="🃏 Таро", payload="menu:tarot"))
#   ...  builder.as_markup()   → кладётся в attachments=[...]
#
# Отличие косметическое: callback_data → payload, Markup → attachments.


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="🃏 Таро (расклад на 3 карты)", payload="menu:tarot"))
    kb.row(CallbackButton(text="🖼️ Пример картинки", payload="menu:image"))
    return kb.as_markup()


def back_menu():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="🔙 Меню", payload="menu:open"))
    return kb.as_markup()


# ───────────────────────── хендлеры ─────────────────────────
# aiogram:  @router.message(CommandStart())     async def(msg: Message)
# maxapi:   @dp.message_created(CommandStart())  async def(event: MessageCreated)


@dp.bot_started()
async def on_started(event: BotStarted) -> None:
    await bot.send_message(
        user_id=event.user.user_id,
        text="✨ Привет! Это прототип Астры на MAX.",
        attachments=[main_menu()],
        format=TextFormat.HTML,  # у maxapi формат задаётся на КАЖДУЮ отправку
    )


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated) -> None:
    await event.message.answer(
        "✨ Я Астра. Выбери, что показать:",
        attachments=[main_menu()],
        format=TextFormat.HTML,
    )


# aiogram:  @router.callback_query(F.data == "menu:tarot")
# maxapi:   @dp.message_callback(F.callback.payload == "menu:tarot")


@dp.message_callback(F.callback.payload == "menu:open")
async def cb_menu(event: MessageCallback) -> None:
    await event.answer()  # обязательное подтверждение (аналог callback.answer())
    await event.edit("✨ Главное меню:", attachments=[main_menu()], format=TextFormat.HTML)


@dp.message_callback(F.callback.payload == "menu:tarot")
async def cb_tarot(event: MessageCallback) -> None:
    await event.answer()

    # ⬇ Ваша реальная логика — тот же вызов, что в bot/handlers/tarot.py
    cards = draw_three()
    spread = cards_to_markdown(cards, question=None)
    # (в бою здесь ушло бы в llm/client.py за трактовкой; для прототипа
    #  показываем сам расклад)

    lines = ["🃏 <b>Твой расклад:</b>", ""]
    for c in cards:
        orient = "перевёрнутая" if c.reversed else "прямая"
        lines.append(f"• <b>{c.position}</b>: {c.name} ({orient})")

    await event.edit("\n".join(lines), attachments=[back_menu()], format=TextFormat.HTML)
    logging.info("Отдали LLM такой контекст:\n%s", spread)


@dp.message_callback(F.callback.payload == "menu:image")
async def cb_image(event: MessageCallback) -> None:
    await event.answer()
    logo = Path(__file__).resolve().parent / "sample.png"
    if logo.exists():
        # Аналог отправки картинки натальной карты (bot/handlers/natal.py).
        await event.message.answer(
            "🖼️ Пример отправки картинки:",
            attachments=[InputMedia(path=str(logo))],
            format=TextFormat.HTML,
        )
    else:
        await event.message.answer(
            "ℹ️ Положи файл prototype_max/sample.png, чтобы увидеть отправку картинки.",
            attachments=[back_menu()],
            format=TextFormat.HTML,
        )


async def main() -> None:
    # long polling — только для разработки; в проде у вас FastAPI-webhook,
    # для него у maxapi есть FastAPIMaxWebhook (пакет maxapi[fastapi]).
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
