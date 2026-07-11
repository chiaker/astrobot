from __future__ import annotations

import html

from aiogram import F, Router
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.bot.keyboards import MENU_BACK_BTN, with_back
from astrobot.bot.platform import Button, Keyboard, PlatformContext
from astrobot.bot.states import SupportFlow
from astrobot.db.models import SupportTicket, User
from astrobot.redis_client import get_redis

router = Router(name="support")

_STATUS = {"open": "🕓 на рассмотрении", "answered": "✅ отвечено"}


def _short(s: str, n: int = 200) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _support_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [Button(text="✍️ Написать обращение", payload="support:new")],
            [MENU_BACK_BTN],
        ]
    )


async def _render_support(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    tickets = list(
        await session.scalars(
            select(SupportTicket)
            .where(SupportTicket.user_id == user.id)
            .order_by(desc(SupportTicket.created_at))
            .limit(10)
        )
    )
    lines = ["🆘 <b>Поддержка</b>", ""]
    if not tickets:
        lines.append("Здесь можно написать нам — мы ответим прямо в боте. Пока обращений нет.")
    else:
        lines.append("Твои обращения:")
        for t in tickets:
            d = t.created_at.strftime("%d.%m.%Y")
            head = "↩️ Возврат" if t.kind == "refund" else "💬 Вопрос"
            lines.append(f"\n{head} · {d} · {_STATUS.get(t.status, t.status)}")
            lines.append(f"<i>{html.escape(_short(t.message))}</i>")
            if t.answer:
                lines.append(f"<b>Ответ:</b> {html.escape(t.answer)}")
    await ctx.edit("\n".join(lines), _support_kb())


@router.callback_query(F.data == "menu:support")
async def on_support(ctx: PlatformContext, session: AsyncSession, user: User) -> None:
    await ctx.answer_callback()
    await _render_support(ctx, session, user)


@router.callback_query(F.data == "support:new")
async def on_support_new(ctx: PlatformContext, state) -> None:
    await ctx.answer_callback()
    await state.set_state(SupportFlow.waiting_for_text)
    await ctx.edit(
        "✍️ Опиши вопрос или проблему одним сообщением — ответим прямо здесь, в боте.",
        with_back([]),
    )


@router.message(SupportFlow.waiting_for_text)
async def on_support_text(
    ctx: PlatformContext,
    state,
    session: AsyncSession,
    user: User,
) -> None:
    text = (ctx.text or "").strip()
    if len(text) < 5:
        await ctx.reply("Напиши чуть подробнее (минимум 5 символов).")
        return
    text = text[:2000]

    # Anti-spam: one ticket per 30s per user.
    redis = get_redis()
    try:
        fresh = await redis.set(f"support:cd:{user.id}", "1", ex=30, nx=True)
    except Exception:
        fresh = True
    if not fresh:
        await ctx.reply("⏳ Секунду — предыдущее обращение ещё отправляется.")
        return

    await state.clear()
    session.add(SupportTicket(user_id=user.id, kind="support", message=text, status="open"))
    await session.commit()

    await ctx.reply(
        "📨 Обращение отправлено — ответим прямо в боте ✨\n"
        "Статус можно посмотреть в меню → 🆘 Поддержка.",
        with_back([]),
    )
