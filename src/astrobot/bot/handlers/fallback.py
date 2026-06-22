from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.handlers.menu import send_main_menu
from astrobot.config import get_settings
from astrobot.db.models import User

router = Router(name="fallback")


@router.message()
async def fallback(message: Message, session: AsyncSession, user: User) -> None:
    # Ops helper: send the bot a GIF/video/file in the ops chat → it replies with
    # the file_id (for WELCOME_ANIMATION). Only active in the configured ops chat,
    # so for everyone else this stays a plain "use the menu" fallback.
    settings = get_settings()
    media = message.animation or message.video or message.document
    if media and settings.ops_chat_id and message.chat.id == settings.ops_chat_id:
        kind = (
            "animation" if message.animation
            else "video" if message.video
            else "document"
        )
        await message.reply(
            f"🆔 <b>file_id</b> ({kind}):\n<code>{media.file_id}</code>\n\n"
            "Для приветствия нужен тип <b>animation</b> — пришли файл как GIF. "
            "Вставь значение в <code>WELCOME_ANIMATION</code> и перезапусти app."
        )
        return

    await message.answer(
        "🌙 Я слушаю через меню — выбери раздел ниже или открой /menu."
    )
    await send_main_menu(message, user, session)
