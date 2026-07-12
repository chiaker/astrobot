from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from astrobot.bot.dispatcher import build_bot, build_dispatcher
from astrobot.config import get_settings
from astrobot.logging_setup import configure_logging
from astrobot.scheduler import build_scheduler
from astrobot.web.routes import broadcasts, health, metrics, payments, stats, telegram

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    from astrobot.bot.platform.telegram import TelegramBot

    bot = build_bot()
    dp = build_dispatcher()
    app.state.bot = bot  # raw aiogram Bot — used by the webhook route for feed_update
    app.state.pbot = TelegramBot(bot)  # PlatformBot — used by the YooKassa webhook
    app.state.dp = dp

    # Native command list + the blue "Menu" button next to the input field.
    try:
        from aiogram.types import BotCommand

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Начать / перезапустить"),
                BotCommand(command="menu", description="Главное меню"),
            ]
        )
    except Exception as e:
        log.warning("set_my_commands_failed", error=str(e))

    polling_task: asyncio.Task | None = None
    scheduler = build_scheduler(app.state.pbot)
    scheduler.start()
    log.info("scheduler_started")

    if settings.run_mode == "webhook":
        # drop_pending_updates=False so a restart doesn't discard messages users
        # sent during the brief downtime — Telegram redelivers them, and the
        # dedupe middleware drops any duplicates.
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=False,
        )
        log.info("webhook_set", url=settings.webhook_url)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        polling_task = asyncio.create_task(dp.start_polling(bot), name="aiogram_polling")
        log.info("polling_started")

    # Startup ping doubles as an ops-alert health check (confirms OPS_CHAT_ID works)
    try:
        from astrobot.alerts import notify_ops

        await notify_ops(bot, f"🟢 Astrobot запущен (mode={settings.run_mode}).")
    except Exception as e:
        log.warning("startup_ping_failed", error=str(e))

    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:
            log.warning("scheduler_shutdown_failed", error=str(e))
        if polling_task is not None:
            polling_task.cancel()
            try:
                await polling_task
            except (asyncio.CancelledError, Exception):
                pass
        # Deliberately DO NOT delete the webhook on shutdown. During a
        # zero-downtime deploy (docker rollout) the new container starts and
        # calls set_webhook BEFORE the old container is drained — if the old one
        # deleted the webhook here, it would wipe the one the new container just
        # set, leaving the bot with no webhook until a manual restart. The
        # webhook is persistent Telegram-side state; each startup re-asserts it
        # (idempotent, same URL), so leaving it in place across restarts is
        # correct and lossless (pending updates stay queued for the next start).
        await bot.session.close()


def _create_max_app(settings) -> FastAPI:
    """MAX platform app: maxapi webhook + admin + YooKassa webhook. Reuses the same
    admin/stats/payments routes as Telegram (they're DB-driven)."""
    from maxapi.webhook.fastapi import FastAPIMaxWebhook

    from astrobot.bot.max_dispatcher import build_max_bot, build_max_dispatcher
    from astrobot.bot.platform.max import MaxBot
    from astrobot.web.admin import setup_admin

    bot = build_max_bot()
    dp = build_max_dispatcher(bot)
    webhook = FastAPIMaxWebhook(dp=dp, bot=bot, secret=settings.webhook_secret)

    @asynccontextmanager
    async def max_lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level)
        app.state.dp = dp
        app.state.bot = None  # MAX webhook is handled by FastAPIMaxWebhook, not app.state
        pbot = MaxBot(bot)
        app.state.pbot = pbot  # PlatformBot — YooKassa webhook + scheduler pushes

        scheduler = build_scheduler(pbot)  # morning horoscope + lunar + reconcile
        scheduler.start()
        log.info("max_scheduler_started")

        # Command menu next to the input field (parity with Telegram's blue Menu).
        try:
            from maxapi.types.command import BotCommand

            await bot.set_my_commands(
                BotCommand(name="start", description="Начать / перезапустить"),
                BotCommand(name="menu", description="Главное меню"),
            )
        except Exception as e:
            log.warning("max_set_commands_failed", error=str(e))

        try:
            from astrobot.alerts import notify_ops

            await notify_ops(pbot, f"🟢 Astrobot MAX запущен (mode={settings.run_mode}).")
        except Exception as e:
            log.warning("max_startup_ping_failed", error=str(e))

        try:
            async with webhook.lifespan(app):
                if settings.run_mode == "webhook":
                    await bot.subscribe_webhook(
                        url=settings.max_webhook_url, secret=settings.webhook_secret
                    )
                    log.info("max_webhook_subscribed", url=settings.max_webhook_url)
                    yield
                else:
                    task = asyncio.create_task(dp.start_polling(bot), name="max_polling")
                    try:
                        yield
                    finally:
                        task.cancel()
        finally:
            try:
                scheduler.shutdown(wait=False)
            except Exception as e:
                log.warning("max_scheduler_shutdown_failed", error=str(e))

    app = FastAPI(title="astrobot-max", lifespan=max_lifespan)
    if settings.admin_secret:
        secure = settings.run_mode == "webhook"
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.admin_secret,
            https_only=secure,
            same_site="strict" if secure else "lax",
        )
    webhook.setup(app, path=settings.max_webhook_path)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(payments.router)
    app.include_router(stats.router)
    app.include_router(broadcasts.router)
    setup_admin(app)
    return app


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.platform == "max":
        return _create_max_app(settings)
    app = FastAPI(title="astrobot", lifespan=lifespan)
    if settings.admin_secret:
        # In prod (webhook mode behind an HTTPS reverse proxy) mark the session
        # cookie Secure + SameSite=strict to harden it against theft/CSRF.
        # In dev (polling, local HTTP) keep it usable over plain HTTP.
        secure_cookie = settings.run_mode == "webhook"
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.admin_secret,
            https_only=secure_cookie,
            same_site="strict" if secure_cookie else "lax",
        )
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(telegram.router)
    app.include_router(payments.router)
    app.include_router(stats.router)
    app.include_router(broadcasts.router)
    return app


app = create_app()
