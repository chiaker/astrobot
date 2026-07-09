"""MAX (maxapi) runtime — mirror of `bot/dispatcher.py` for the MAX platform.

Reuses the SAME `ctx`-migrated handler bodies from `bot/handlers/*`. maxapi calls
handlers as `handler(event, **kwargs)` where kwargs are `data` keys matching the
handler's parameter names (event is positional-first, like aiogram). So we register
thin wrappers that take the event first + the injected deps (`ctx`, `session`,
`user`, `context`) and delegate to the shared handler bodies.

Only handlers already migrated to `ctx` are wired here (menu / about / legal). The
rest join as they migrate off aiogram-only `Message`/`CallbackQuery`.

maxapi specifics (verified against maxapi 1.x):
- `Bot(token, format=TextFormat.HTML)` sets the default parse format globally.
- Middlewares: `BaseMiddleware.__call__(self, handler, event, data)` — populate `data`.
- FSM: maxapi injects the context object into `data["context"]` (duck-types our
  StateStore: get_state/set_state/get_data/update_data/clear). `data["state"]` is the
  raw state VALUE, not the context — so we pass `context` as the handlers' `state` arg.
"""
from __future__ import annotations

import structlog
from maxapi import Bot, Dispatcher, F
from maxapi.context.context import MemoryContext
from maxapi.enums.parse_mode import TextFormat
from maxapi.filters.command import Command, CommandStart
from maxapi.filters.middleware import BaseMiddleware
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from sqlalchemy import select

from astrobot.bot.handlers import about as h_about
from astrobot.bot.handlers import legal as h_legal
from astrobot.bot.handlers import menu as h_menu
from astrobot.bot.platform.max import MaxContext, to_markup
from astrobot.config import get_settings
from astrobot.db.models import User
from astrobot.db.session import get_sessionmaker
from astrobot.referral import generate_code

log = structlog.get_logger(__name__)


def build_max_bot() -> Bot:
    settings = get_settings()
    return Bot(token=settings.bot_token, format=TextFormat.HTML)


# ─────────────────────────── helpers ───────────────────────────


def _external_user(event) -> tuple[int, str | None] | None:
    """(user_id, username) from any maxapi event, platform-agnostically."""
    cb = getattr(event, "callback", None)
    if cb is not None and getattr(cb, "user", None) is not None:
        return cb.user.user_id, cb.user.username
    msg = getattr(event, "message", None)
    if msg is not None and getattr(msg, "sender", None) is not None:
        return msg.sender.user_id, msg.sender.username
    u = getattr(event, "user", None)  # bot_started
    if u is not None:
        return u.user_id, getattr(u, "username", None)
    return None


async def _unique_code(session) -> str:
    for _ in range(5):
        code = generate_code()
        if await session.scalar(select(User.id).where(User.referral_code == code)) is None:
            return code
    return generate_code()


# ─────────────────────────── middlewares ───────────────────────────


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with get_sessionmaker()() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Get-or-create the application User by the MAX user id.

    NOTE: on the MAX deploy the `users.tg_user_id` column holds the MAX user id
    (separate database — the column is effectively an external id; a rename to
    `external_user_id` is a later cosmetic migration)."""

    async def __call__(self, handler, event, data):
        info = _external_user(event)
        session = data.get("session")
        if info is None or session is None:
            return await handler(event, data)
        ext_id, username = info
        user = await session.scalar(select(User).where(User.tg_user_id == ext_id))
        is_new = user is None
        if is_new:
            user = User(
                tg_user_id=ext_id,
                username=username,
                lang="ru",
                referral_code=await _unique_code(session),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        elif user.username != username:
            user.username = username
            await session.commit()
        data["user"] = user
        data["is_new_user"] = is_new
        return await handler(event, data)


class ContextMiddleware(BaseMiddleware):
    """Inject a platform-neutral `ctx: PlatformContext` (MaxContext) for handlers."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def __call__(self, handler, event, data):
        ctx = None
        if isinstance(event, MessageCallback):
            ctx = MaxContext(bot=self._bot, callback=event)
        elif isinstance(event, MessageCreated):
            ctx = MaxContext(bot=self._bot, message=event)
        if ctx is not None:
            data["ctx"] = ctx
        try:
            return await handler(event, data)
        finally:
            # Flush a deferred callback ack (no-op unless it was a callback the
            # handler didn't otherwise answer) — even on error, so the button's
            # pending state always clears.
            if ctx is not None:
                await ctx.finish()


# ─────────────────────────── dispatcher ───────────────────────────


def build_max_dispatcher(bot: Bot) -> Dispatcher:
    # MemoryContext for now (no external dep); switch to RedisContext(url=...) for
    # prod so FSM survives restarts — TODO(max): confirm RedisContext storage kwargs.
    dp = Dispatcher(storage=MemoryContext)
    dp.middleware(DbSessionMiddleware())
    dp.middleware(UserMiddleware())
    dp.middleware(ContextMiddleware(bot))

    # --- entry: first contact + /start + /menu → main menu ---
    # NOTE: on Telegram /start runs onboarding; that handler isn't ctx-migrated yet,
    # so on MAX /start shows the menu for now. Wire onboarding once it's on ctx.

    @dp.bot_started()
    async def _on_bot_started(event: BotStarted, session, user):
        text, kb = await h_menu.render_main_menu(user, session)
        await bot.send_message(
            user_id=user.tg_user_id, text=text,
            attachments=[to_markup(kb)], format=TextFormat.HTML,
        )

    @dp.message_created(CommandStart())
    async def _on_start(event: MessageCreated, ctx, session, user):
        await h_menu.cmd_menu(ctx, session, user)

    @dp.message_created(Command("menu"))
    async def _on_menu_cmd(event: MessageCreated, ctx, session, user):
        await h_menu.cmd_menu(ctx, session, user)

    # --- callbacks: delegate to shared ctx handler bodies ---
    # maxapi injects `context` (FSM) which duck-types our StateStore → pass as `state`.

    @dp.message_callback(F.callback.payload == "menu:open")
    async def _cb_menu_open(event: MessageCallback, ctx, context, session, user):
        await h_menu.on_menu_open(ctx, context, session, user)

    @dp.message_callback(F.callback.payload == "menu:new")
    async def _cb_menu_new(event: MessageCallback, ctx, context, session, user):
        await h_menu.on_menu_new(ctx, context, session, user)

    @dp.message_callback(F.callback.payload == "menu:about")
    async def _cb_about(event: MessageCallback, ctx):
        await h_about.on_about(ctx)

    @dp.message_callback(F.callback.payload == "referral:show")
    async def _cb_referral(event: MessageCallback, ctx, user):
        await h_about.on_referral_show(ctx, user)

    @dp.message_callback(F.callback.payload == "legal:privacy")
    async def _cb_privacy(event: MessageCallback, ctx):
        await h_legal.cb_privacy(ctx)

    @dp.message_callback(F.callback.payload == "legal:terms")
    async def _cb_terms(event: MessageCallback, ctx):
        await h_legal.cb_terms(ctx)

    log.info("max_dispatcher_built")
    return dp
