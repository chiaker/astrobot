from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from astrobot.bot.dispatcher import build_bot, build_dispatcher
from astrobot.config import get_settings
from astrobot.logging_setup import configure_logging
from astrobot.scheduler import build_scheduler
from astrobot.web.admin import setup_admin
from astrobot.web.routes import health, metrics, payments, stats, telegram

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    bot = build_bot()
    dp = build_dispatcher()
    app.state.bot = bot
    app.state.dp = dp

    polling_task: asyncio.Task | None = None
    scheduler = build_scheduler(bot)
    scheduler.start()
    log.info("scheduler_started")

    if settings.run_mode == "webhook":
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        log.info("webhook_set", url=settings.webhook_url)
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        polling_task = asyncio.create_task(dp.start_polling(bot), name="aiogram_polling")
        log.info("polling_started")

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
        if settings.run_mode == "webhook":
            try:
                await bot.delete_webhook()
                log.info("webhook_deleted")
            except Exception as e:
                log.warning("webhook_delete_failed", error=str(e))
        await bot.session.close()


def create_app() -> FastAPI:
    app = FastAPI(title="astrobot", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(telegram.router)
    app.include_router(payments.router)
    # IMPORTANT: stats router must be registered before setup_admin() so that
    # the explicit /admin/stats route wins over sqladmin's catch-all at /admin.
    app.include_router(stats.router)
    setup_admin(app)
    return app


app = create_app()
