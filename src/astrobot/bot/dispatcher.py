from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

from astrobot.bot.middlewares import (
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
        fallback,
        horoscope,
        natal,
        onboarding,
        payment,
        profile,
        question,
        response_toggle,
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

    dp.include_router(errors.router)
    dp.include_router(onboarding.router)
    dp.include_router(profile.router)
    dp.include_router(natal.router)
    dp.include_router(horoscope.router)
    dp.include_router(question.router)
    dp.include_router(payment.router)
    dp.include_router(about.router)
    dp.include_router(response_toggle.router)
    dp.include_router(fallback.router)
    return dp
