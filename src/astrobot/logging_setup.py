from __future__ import annotations

import logging
from typing import Any

import structlog

PII_KEYS: frozenset[str] = frozenset(
    {
        "question",
        "answer",
        "text",
        "message_text",
        "birth_date",
        "birth_time",
        "lat",
        "lon",
        "city_name",
        "city_input",
        "city_display",
        "name",
        "full_name",
        "first_name",
        "last_name",
        "username",
        "phone",
        "email",
    }
)


def _mask_pii(_, __, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() not in PII_KEYS:
            continue
        value = event_dict[key]
        if value is None:
            continue
        if isinstance(value, str):
            event_dict[key] = (value[:1] + "***") if value else "***"
        elif isinstance(value, (int, float)):
            event_dict[key] = "***"
        else:
            event_dict[key] = "<pii>"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    level_int = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=level_int)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _mask_pii,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        cache_logger_on_first_use=True,
    )
