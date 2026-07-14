"""MAX (maxapi) runtime — mirror of `bot/dispatcher.py` for the MAX platform.

Reuses the SAME `ctx`-migrated handler bodies from `bot/handlers/*`. maxapi calls
handlers as `handler(event, **kwargs)` where kwargs are `data` keys matching the
handler's parameter names (event positional-first, like aiogram). So every handler
is registered via a thin generic wrapper (`_wrap`) that absorbs the event and
forwards only the deps the body declares (`ctx`, `state`, `session`, `user`, `pbot`,
`is_new_user`).

State: maxapi injects the FSM context as `data["context"]` (duck-types StateStore).
We wrap it in `MaxState` (normalizes aiogram `State` objects → their string key), so
`state.set_state(Onboarding.waiting_for_name)` stores the SAME string aiogram would,
and `StateFilter(<State>.state)` matches it.

Telegram Stars handlers are intentionally NOT wired (MAX has no Stars checkout).
"""
from __future__ import annotations

import inspect
from functools import cache

import structlog
from maxapi import Bot, Dispatcher, F
from maxapi.context.context import RedisContext
from maxapi.enums.parse_mode import TextFormat
from maxapi.filters.command import Command, CommandStart
from maxapi.filters.middleware import BaseMiddleware
from maxapi.filters.state import StateFilter
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from sqlalchemy import select

from astrobot.bot.handlers import about as h_about
from astrobot.bot.handlers import broadcast as h_bcast
from astrobot.bot.handlers import compatibility as h_compat
from astrobot.bot.handlers import favorites as h_fav
from astrobot.bot.handlers import horoscope as h_horo
from astrobot.bot.handlers import legal as h_legal
from astrobot.bot.handlers import menu as h_menu
from astrobot.bot.handlers import natal as h_natal
from astrobot.bot.handlers import onboarding as h_onb
from astrobot.bot.handlers import payment as h_pay
from astrobot.bot.handlers import profile as h_profile
from astrobot.bot.handlers import question as h_q
from astrobot.bot.handlers import support as h_support
from astrobot.bot.handlers import tarot as h_tarot
from astrobot.bot.keyboards import name_skip_kb, onboarding_start_kb
from astrobot.bot.platform.max import MaxBot, MaxContext, MaxState, to_markup
from astrobot.bot.states import (
    AskingQuestion,
    CompatFlow,
    Onboarding,
    PaymentFlow,
    PushSetup,
    SupportFlow,
    TarotFlow,
)
from astrobot.config import get_settings
from astrobot.db.models import BirthProfile, User
from astrobot.db.session import get_sessionmaker
from astrobot.redis_client import get_redis
from astrobot.referral import generate_code, parse_start_arg, try_apply_referral

log = structlog.get_logger(__name__)


def build_max_bot() -> Bot:
    return Bot(token=get_settings().bot_token, format=TextFormat.HTML)


# ─────────────────────────── helpers ───────────────────────────


def _external_user(event) -> tuple[int, str | None] | None:
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


@cache
def _params(func) -> frozenset:
    return frozenset(inspect.signature(func).parameters)


def _wrap(body):
    """Adapt a ctx handler body to a maxapi handler (event-first + data kwargs)."""

    async def _handler(event, ctx=None, context=None, session=None, user=None, pbot=None, is_new_user=False):
        avail = {
            "ctx": ctx,
            "state": MaxState(context) if context is not None else None,
            "session": session,
            "user": user,
            "pbot": pbot,
            "is_new_user": is_new_user,
        }
        wanted = _params(body)
        await body(**{k: v for k, v in avail.items() if k in wanted})

    return _handler


# ─────────────────────────── middlewares ───────────────────────────


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with get_sessionmaker()() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    """Get-or-create the User by MAX user id (stored in tg_user_id on the MAX DB)."""

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
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._pbot = MaxBot(bot)

    async def __call__(self, handler, event, data):
        ctx = None
        if isinstance(event, MessageCallback):
            ctx = MaxContext(bot=self._bot, callback=event)
        elif isinstance(event, MessageCreated):
            ctx = MaxContext(bot=self._bot, message=event)
        if ctx is not None:
            data["ctx"] = ctx
        data["pbot"] = self._pbot
        try:
            return await handler(event, data)
        finally:
            if ctx is not None:
                await ctx.finish()


# ─────────────────────────── dispatcher ───────────────────────────

# Exact-payload callbacks → handler body.
_CB_EXACT = {
    "menu:open": h_menu.on_menu_open,
    "menu:new": h_menu.on_menu_new,
    "menu:about": h_about.on_about,
    "referral:show": h_about.on_referral_show,
    "legal:privacy": h_legal.cb_privacy,
    "legal:terms": h_legal.cb_terms,
    "menu:natal": h_natal.on_natal,
    "natal:regen": h_natal.on_natal_regen,
    "menu:horoscope": h_horo.on_horoscope_menu,
    "menu:question": h_q.on_question_button,
    "chat:exit": h_q.on_chat_exit,
    "chat:own_question": h_q.on_own_question,
    "show_topics": h_q.on_show_topics,
    "menu:tarot": h_tarot.on_tarot_menu,
    "tarot:new": h_tarot.on_tarot_new,
    "tarot:draw": h_tarot.on_tarot_draw,
    "menu:compatibility": h_compat.on_compat_menu,
    "compat:new": h_compat.on_compat_new,
    "compat:time:unknown": h_compat.on_compat_time_unknown,
    "menu:favorites": h_fav.on_favorites_menu,
    "menu:support": h_support.on_support,
    "support:new": h_support.on_support_new,
    "menu:premium": h_pay.on_premium,
    "premium:show": h_pay.on_premium_inline,
    "sub:cancel": h_pay.on_sub_cancel,
    "pay:cancel": h_pay.on_pay_cancel,
    "menu:profile": h_profile.on_profile,
    "menu:settings": h_profile.on_settings,
    "payments:mine": h_profile.on_my_payments,
    "settings:gender": h_profile.on_gender_toggle,
    "settings:astro_terms": h_profile.on_astro_terms_toggle,
    "settings:push_lunar": h_profile.on_push_lunar_toggle,
    "settings:push_horoscope": h_profile.on_push_horoscope_toggle,
    "push:setup_start": h_profile.on_push_setup_start,
    "push:cancel": h_profile.on_push_cancel,
    "profile:reset": h_profile.on_profile_reset_warn,
    "profile:reset:confirm": h_profile.on_profile_reset_confirm,
    "bcast:chat": h_bcast.on_broadcast_chat,
    "bcast:onb": h_bcast.on_broadcast_onboarding,
    "onb:name:skip": h_onb.on_name_skip,
    "time:unknown": h_onb.on_time_unknown,
    "onb:save": h_onb.on_confirm_save,
    "onb:restart": h_onb.on_confirm_restart,
    "onb:final:ok": h_onb.on_final_ok,
    "onb:final:restart": h_onb.on_final_restart,
    "cancel": h_onb.on_cancel,
}

# Prefix-payload callbacks (registered AFTER exacts so e.g. pay:cancel wins).
_CB_PREFIX = {
    "horo:": h_horo.on_horoscope_period,
    "topic:": h_q.on_topic,
    "q:": h_q.on_question_pick,
    "fav:save:": h_fav.on_save,
    "fav:view:": h_fav.on_view,
    "fav:del:": h_fav.on_delete,
    "buy:": h_pay.on_buy,
    "pay:": h_pay.on_pay,
    "push:hour:": h_profile.on_push_hour,
    "onb:gender:": h_onb.on_gender,
    "onb:terms:": h_onb.on_astro_terms,
    "bcast:ask:": h_bcast.on_broadcast_ask,
    "bcast:buy:": h_bcast.on_broadcast_buy,
}

# FSM message handlers: state string → handler body.
_MSG_STATES = {
    Onboarding.waiting_for_name.state: h_onb.on_name,
    Onboarding.waiting_for_date.state: h_onb.on_date,
    Onboarding.waiting_for_time.state: h_onb.on_time,
    Onboarding.waiting_for_city.state: h_onb.on_city,
    AskingQuestion.waiting_for_text.state: h_q.on_question_text,
    TarotFlow.waiting_for_question.state: h_tarot.on_tarot_question,
    CompatFlow.waiting_for_name.state: h_compat.on_compat_name,
    CompatFlow.waiting_for_date.state: h_compat.on_compat_date,
    CompatFlow.waiting_for_time.state: h_compat.on_compat_time,
    CompatFlow.waiting_for_city.state: h_compat.on_compat_city,
    SupportFlow.waiting_for_text.state: h_support.on_support_text,
    PaymentFlow.waiting_for_email.state: h_pay.on_payment_email,
    PushSetup.waiting_for_city.state: h_profile.on_push_city,
}


def build_max_dispatcher(bot: Bot) -> Dispatcher:
    # Redis-backed FSM so in-progress flows survive restarts (same Redis as TG).
    # use_create_task: process each update in a background task so the webhook
    # returns 200 immediately — LLM handlers (~30s) would otherwise exceed MAX's
    # webhook timeout and get re-delivered (duplicate replies).
    # ponytail: concurrent per-user updates possible; the expensive LLM path is
    # already serialized by user_llm_lock.
    dp = Dispatcher(storage=RedisContext, redis_client=get_redis(), use_create_task=True)
    dp.middleware(DbSessionMiddleware())
    dp.middleware(UserMiddleware())
    dp.middleware(ContextMiddleware(bot))

    # bot_started → onboarding (new user) or main menu (build via bot since there's
    # no ctx for this event; the FSM `context` is keyed the same as the user's DM
    # messages, so setting the state here carries over to their next reply).
    @dp.bot_started()
    async def _on_bot_started(event, session, user, is_new_user=False, context=None):
        # Referral deep link arrives in bot_started.payload on MAX (not /start text).
        payload = getattr(event, "payload", None)
        code = parse_start_arg(f"/start {payload}") if payload else None
        if code and is_new_user:
            applied = await try_apply_referral(session, user, code)
            if applied is not None:
                inviter, credited = applied
                await session.commit()
                await bot.send_message(
                    user_id=user.tg_user_id,
                    text="🎁 Друг тебя пригласил — я добавила <b>+2 бесплатных вопроса</b> ✨",
                    format=TextFormat.HTML,
                )
                if credited:
                    try:
                        await bot.send_message(
                            user_id=inviter.tg_user_id,
                            text="🎁 По твоей реферальной ссылке зарегистрировался новый "
                            "пользователь — тебе <b>+2 бесплатных вопроса</b>! ✨",
                            format=TextFormat.HTML,
                        )
                    except Exception:
                        pass
        # New user (no profile) → start onboarding immediately, mirroring Telegram's
        # auto-/start. MAX has no /start on open, so we set the FSM state here and
        # send the name prompt; the user's next message enters the flow. If the FSM
        # context is somehow missing, fall back to a start button.
        profile = await session.get(BirthProfile, user.id)
        if profile is None:
            if context is not None:
                state = MaxState(context)
                await state.update_data(
                    display_name=user.display_name,
                    gender=user.gender,
                    astro_terms=user.astro_terms_enabled,
                )
                await bot.send_message(
                    user_id=user.tg_user_id,
                    text=h_onb.onboarding_welcome_text(user),
                    attachments=[to_markup(name_skip_kb())],
                    format=TextFormat.HTML,
                )
                await state.set_state(Onboarding.waiting_for_name)
            else:
                await bot.send_message(
                    user_id=user.tg_user_id,
                    text=(
                        "🌙 Привет! Я Astra — твой астролог. Нажми кнопку ниже, "
                        "чтобы познакомиться ✨"
                    ),
                    attachments=[to_markup(onboarding_start_kb())],
                    format=TextFormat.HTML,
                )
            return
        text, kb = await h_menu.render_main_menu(user, session)
        await bot.send_message(
            user_id=user.tg_user_id, text=text,
            attachments=[to_markup(kb)], format=TextFormat.HTML,
        )

    # Commands.
    dp.message_created(CommandStart())(_wrap(h_onb.cmd_start))
    dp.message_created(Command("menu"))(_wrap(h_menu.cmd_menu))
    dp.message_created(Command("cancel"))(_wrap(h_onb.cmd_cancel))
    dp.message_created(Command("privacy"))(_wrap(h_legal.cmd_privacy))
    dp.message_created(Command("terms"))(_wrap(h_legal.cmd_terms))

    # FSM message handlers (state-filtered).
    for state_str, body in _MSG_STATES.items():
        dp.message_created(StateFilter(state_str))(_wrap(body))

    # Fallback: any other text → main menu (registered before callbacks is fine;
    # commands + state handlers above are matched first).
    @dp.message_created()
    async def _fallback(event, ctx, session, user):
        await h_menu.show_main_menu(ctx, user, session)

    # Callbacks — exact first, then prefixes.
    for payload, body in _CB_EXACT.items():
        dp.message_callback(F.callback.payload == payload)(_wrap(body))
    for prefix, body in _CB_PREFIX.items():
        dp.message_callback(F.callback.payload.startswith(prefix))(_wrap(body))

    log.info("max_dispatcher_built", callbacks=len(_CB_EXACT) + len(_CB_PREFIX), states=len(_MSG_STATES))
    return dp
