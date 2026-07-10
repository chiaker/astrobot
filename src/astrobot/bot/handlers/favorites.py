from __future__ import annotations

from aiogram import F, Router
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.formatting import md_to_telegram_html
from astrobot.bot.keyboards import MENU_BACK_BTN, with_back
from astrobot.bot.platform import Button, Keyboard, PlatformContext
from astrobot.bot.responses import chunk_text
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
async def on_save(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    try:
        rid = int((ctx.payload or "").split(":", 2)[2])
    except (ValueError, AttributeError, IndexError):
        await ctx.answer_callback("Кнопка устарела")
        return

    resp = await session.get(Response, rid)
    if resp is None or resp.user_id != user.id:
        await ctx.answer_callback("Ответ больше недоступен")
        return

    # Limit check for free users
    if not is_premium(user):
        used = await session.scalar(
            select(func.count(Favorite.id)).where(Favorite.user_id == user.id)
        )
        if used is not None and used >= FREE_LIMIT:
            await ctx.answer_callback(
                f"Лимит избранного на free-тарифе ({FREE_LIMIT}). "
                f"Удали что-нибудь или открой Премиум.",
                alert=True,
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
        await ctx.answer_callback("Уже в избранном")
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
    await ctx.answer_callback("⭐ Сохранено в избранное")


def _list_kb(items: list[Favorite]) -> Keyboard:
    rows: list[list[Button]] = []
    for f in items:
        date = f.created_at.strftime("%d.%m")
        rows.append([Button(text=f"{f.label} • {date}", payload=f"fav:view:{f.id}")])
    rows.append([MENU_BACK_BTN])
    return Keyboard.from_rows(rows)


@router.callback_query(F.data == "menu:favorites")
async def on_favorites_menu(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    items = list(
        await session.scalars(
            select(Favorite)
            .where(Favorite.user_id == user.id)
            .order_by(desc(Favorite.created_at))
            .limit(20)
        )
    )
    if not items:
        await ctx.edit(
            "⭐ Здесь будет то, что ты сохранишь. Нажми «⭐ Сохранить» под любым "
            "ответом Астры, и он окажется тут.",
            with_back([]),
        )
        return

    intro = "⭐ <b>Твоё избранное</b>\n\nВыбери, чтобы перечитать:"
    if not is_premium(user):
        intro += f"\n<i>Использовано {len(items)} из {FREE_LIMIT} (free-тариф).</i>"
    await ctx.edit(intro, _list_kb(items))


@router.callback_query(F.data.startswith("fav:view:"))
async def on_view(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    try:
        fid = int((ctx.payload or "").split(":", 2)[2])
    except (ValueError, AttributeError, IndexError):
        await ctx.answer_callback("Кнопка устарела")
        return

    fav = await session.get(Favorite, fid)
    if fav is None or fav.user_id != user.id:
        await ctx.answer_callback("Не найдено")
        return

    await ctx.answer_callback()
    kb = Keyboard.from_rows(
        [
            [Button(text="🗑 Удалить из избранного", payload=f"fav:del:{fav.id}")],
            [MENU_BACK_BTN],
        ]
    )
    header = f"⭐ <i>{fav.label} • {fav.created_at.strftime('%d.%m.%Y')}</i>\n\n"
    text = header + md_to_telegram_html(fav.full)
    chunks = chunk_text(text)
    for i, chunk in enumerate(chunks):
        await ctx.reply(chunk, kb if i == len(chunks) - 1 else None)


@router.callback_query(F.data.startswith("fav:del:"))
async def on_delete(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    try:
        fid = int((ctx.payload or "").split(":", 2)[2])
    except (ValueError, AttributeError, IndexError):
        await ctx.answer_callback("Кнопка устарела")
        return

    fav = await session.get(Favorite, fid)
    if fav is None or fav.user_id != user.id:
        await ctx.answer_callback("Не найдено")
        return

    await session.delete(fav)
    await session.commit()
    await ctx.answer_callback("Удалено из избранного")
