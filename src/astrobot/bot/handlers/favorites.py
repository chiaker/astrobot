from __future__ import annotations

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html
from astrobot.bot.keyboards import MENU_BACK_BTN, with_back
from astrobot.bot.responses import chunk_text, edit_or_send, safe_answer
from astrobot.db.models import Favorite, Response, User
from astrobot.limits import is_premium
from astrobot.metrics import FAVORITES_SAVED

router = Router(name="favorites")

FREE_LIMIT = 5

_LABEL_BY_KIND: dict[str, str] = {
    "natal": "🌟 Натальная карта",
    "horoscope:today": "🔮 Гороскоп на день",
    "horoscope:week": "🔮 Гороскоп на неделю",
    "horoscope:month": "🔮 Гороскоп на месяц",
    "question": "💬 Ответ Астры",
    "tarot": "🃏 Расклад Таро",
    "compatibility": "💞 Совместимость",
}


def _label_for_kind(kind: str) -> str:
    return _LABEL_BY_KIND.get(kind, "✨ Сохранённое")


@router.callback_query(F.data.startswith("fav:save:"))
async def on_save(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    try:
        rid = int(call.data.split(":", 2)[2])
    except (ValueError, AttributeError):
        await call.answer("Кнопка устарела", show_alert=False)
        return

    resp = await session.get(Response, rid)
    if resp is None or resp.user_id != user.id:
        await call.answer("Ответ больше недоступен", show_alert=False)
        return

    # Limit check for free users
    if not is_premium(user):
        used = await session.scalar(
            select(func.count(Favorite.id)).where(Favorite.user_id == user.id)
        )
        if used is not None and used >= FREE_LIMIT:
            await call.answer(
                f"Лимит избранного на free-тарифе ({FREE_LIMIT}). "
                f"Удали что-нибудь или открой Премиум.",
                show_alert=True,
            )
            return

    # Dedupe: don't save the same response twice
    existing = await session.scalar(
        select(Favorite).where(
            Favorite.user_id == user.id,
            Favorite.kind == resp.kind,
            Favorite.brief == resp.brief,
        )
    )
    if existing is not None:
        await call.answer("Уже в избранном", show_alert=False)
        return

    label = _label_for_kind(resp.kind)
    fav = Favorite(
        user_id=user.id,
        kind=resp.kind,
        label=label,
        brief=resp.brief,
        full=resp.full,
    )
    session.add(fav)
    await session.commit()
    FAVORITES_SAVED.inc()
    await call.answer("⭐ Сохранено в избранное")


def _list_kb(items: list[Favorite]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for f in items:
        date = f.created_at.strftime("%d.%m")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{f.label} • {date}", callback_data=f"fav:view:{f.id}"
                )
            ]
        )
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:favorites")
async def on_favorites_menu(call: CallbackQuery, session: AsyncSession, user: User) -> None:
    await call.answer()
    items = list(
        await session.scalars(
            select(Favorite)
            .where(Favorite.user_id == user.id)
            .order_by(desc(Favorite.created_at))
            .limit(20)
        )
    )
    if not items:
        await edit_or_send(
            call,
            "⭐ Здесь будет то, что ты сохранишь. Нажми «⭐ Сохранить» под любым "
            "ответом Астры, и он окажется тут.",
            with_back([]),
        )
        return

    intro = "⭐ <b>Твоё избранное</b>\n\nВыбери, чтобы перечитать:"
    if not is_premium(user):
        intro += f"\n<i>Использовано {len(items)} из {FREE_LIMIT} (free-тариф).</i>"
    await edit_or_send(call, intro, _list_kb(items))


@router.callback_query(F.data.startswith("fav:view:"))
async def on_view(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    try:
        fid = int(call.data.split(":", 2)[2])
    except (ValueError, AttributeError):
        await call.answer("Кнопка устарела", show_alert=False)
        return

    fav = await session.get(Favorite, fid)
    if fav is None or fav.user_id != user.id:
        await call.answer("Не найдено", show_alert=False)
        return

    await call.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Удалить из избранного", callback_data=f"fav:del:{fav.id}"
                )
            ],
            [MENU_BACK_BTN],
        ]
    )
    header = f"⭐ <i>{fav.label} • {fav.created_at.strftime('%d.%m.%Y')}</i>\n\n"
    text = header + md_to_telegram_html(fav.full)
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        await safe_answer(
            call.message, chunk, reply_markup=kb if i == len(chunks) - 1 else None
        )


@router.callback_query(F.data.startswith("fav:del:"))
async def on_delete(
    call: CallbackQuery,
    session: AsyncSession,
    user: User,
) -> None:
    try:
        fid = int(call.data.split(":", 2)[2])
    except (ValueError, AttributeError):
        await call.answer("Кнопка устарела", show_alert=False)
        return

    fav = await session.get(Favorite, fid)
    if fav is None or fav.user_id != user.id:
        await call.answer("Не найдено", show_alert=False)
        return

    await session.delete(fav)
    await session.commit()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("Удалено из избранного")
