from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

from astrobot.bot.middlewares import (
    ContextMiddleware,
    DbSessionMiddleware,
    UpdateDedupeMiddleware,
    UserMiddleware,
)
from astrobot.config import get_settings


def build_bot() -> Bot:
    settings = get_settings()
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    from astrobot.bot import errors
    from astrobot.bot.handlers import (
        about,
        broadcast,
        compatibility,
        fallback,
        favorites,
        horoscope,
        legal,
        menu,
        natal,
        onboarding,
        payment,
        profile,
        question,
        response_toggle,
        support,
        tarot,
    )

    settings = get_settings()
    storage = RedisStorage.from_url(
        settings.redis_url,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )
    dp = Dispatcher(storage=storage)

    dp.update.middleware(UpdateDedupeMiddleware())
    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(UserMiddleware())
    # Inject platform-neutral `ctx` for handlers migrated to the platform layer.
    # Additive: non-migrated handlers keep using message/callback_query directly.
    dp.message.middleware(ContextMiddleware())
    dp.callback_query.middleware(ContextMiddleware())

    dp.include_router(errors.router)
    dp.include_router(legal.router)
    dp.include_router(onboarding.router)
    dp.include_router(menu.router)
    dp.include_router(profile.router)
    dp.include_router(natal.router)
    dp.include_router(horoscope.router)
    dp.include_router(question.router)
    dp.include_router(broadcast.router)
    dp.include_router(tarot.router)
    dp.include_router(compatibility.router)
    dp.include_router(payment.router)
    dp.include_router(about.router)
    dp.include_router(favorites.router)
    dp.include_router(support.router)
    dp.include_router(response_toggle.router)
    dp.include_router(fallback.router)

    _adapt_ctx_handlers(dp)
    return dp


def _adapt_ctx_handlers(dp: Dispatcher) -> None:
    """Make platform-neutral (`ctx`-first) handlers callable by aiogram.

    aiogram always binds the update to the handler's first positional param
    (see CallableObject.call). Our migrated handlers take `ctx` there while the
    ContextMiddleware also injects `ctx` via data — so aiogram passes it both
    positionally and by keyword: `TypeError: got multiple values for 'ctx'`.
    For handlers that don't take the raw event (no `message`/`callback_query`
    param) we drop the positional event and call purely by keyword — the same
    contract as the MAX dispatcher's `_wrap`. Handlers that still use the raw
    event (fallback, successful-payment, the error handler) are left untouched.
    """

    def _drop_event(orig):
        async def _adapted(_event, **kwargs):
            return await orig(**kwargs)

        return _adapted

    def _walk(router: Router) -> None:
        for name in ("message", "callback_query"):
            for handler in router.observers[name].handlers:
                if {"message", "callback_query"}.isdisjoint(handler.params):
                    handler.callback = _drop_event(handler.callback)
        for sub in router.sub_routers:
            _walk(sub)

    _walk(dp)
