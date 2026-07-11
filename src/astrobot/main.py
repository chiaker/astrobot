import argparse

import uvicorn

from astrobot.config import get_settings


def _set_webhook(settings) -> None:
    """Register the Telegram webhook from settings and exit. Run as a post-deploy
    step (see .github/workflows/deploy.yml) AFTER the zero-downtime swap so the
    webhook always points at the live container — independent of any container's
    startup/shutdown timing, which can otherwise leave it unset after a rollout."""
    import asyncio

    from astrobot.bot.dispatcher import build_bot

    async def _run() -> None:
        bot = build_bot()
        try:
            await bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                drop_pending_updates=False,
            )
            info = await bot.get_webhook_info()
            print(
                f"webhook set: url={info.url} "
                f"pending={info.pending_update_count} last_error={info.last_error_message}"
            )
        finally:
            await bot.session.close()

    asyncio.run(_run())


def main() -> None:
    parser = argparse.ArgumentParser(prog="astrobot")
    parser.add_argument(
        "--mode",
        choices=["polling", "webhook"],
        default=None,
        help="Override RUN_MODE from .env",
    )
    parser.add_argument(
        "--set-webhook",
        action="store_true",
        help="Register the Telegram webhook (from settings) and exit. "
        "Used as a post-deploy step so the webhook always points at the live "
        "container after a zero-downtime swap.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.mode:
        import os

        os.environ["RUN_MODE"] = args.mode
        get_settings.cache_clear()
        settings = get_settings()

    if args.set_webhook:
        _set_webhook(settings)
        return

    # Both platforms run the FastAPI app (webhook/admin); create_app() branches on
    # PLATFORM. MAX uses maxapi's webhook + polling fallback (see web/app.py).
    uvicorn.run(
        "astrobot.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
