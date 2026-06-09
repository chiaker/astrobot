import argparse

import uvicorn

from astrobot.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="astrobot")
    parser.add_argument(
        "--mode",
        choices=["polling", "webhook"],
        default=None,
        help="Override RUN_MODE from .env",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.mode:
        import os

        os.environ["RUN_MODE"] = args.mode
        get_settings.cache_clear()
        settings = get_settings()

    uvicorn.run(
        "astrobot.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
