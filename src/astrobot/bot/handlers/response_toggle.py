from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.responses import replace_response
from astrobot.db.models import Response, User

router = Router(name="response_toggle")


@router.callback_query(F.data.startswith("resp:"))
async def on_toggle(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    try:
        _, raw_id, target = call.data.split(":", 2)
        rid = int(raw_id)
    except (ValueError, AttributeError):
        await call.answer("Кнопка устарела", show_alert=False)
        return

    if target not in {"brief", "full"}:
        await call.answer()
        return

    resp = await session.get(Response, rid)
    if resp is None or resp.user_id != user.id:
        await call.answer("Ответ больше недоступен", show_alert=False)
        return

    await replace_response(call.message, session, user, resp, target)
    await call.answer()
