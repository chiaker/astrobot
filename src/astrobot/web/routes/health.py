from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.session import get_session
from astrobot.redis_client import get_redis

router = APIRouter(tags=["health"])
log = structlog.get_logger(__name__)


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    db_ok = False
    redis_ok = False
    detail: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        detail["db"] = str(e)
        log.warning("health_db_fail", error=str(e))

    try:
        pong = await get_redis().ping()
        redis_ok = bool(pong)
    except Exception as e:
        detail["redis"] = str(e)
        log.warning("health_redis_fail", error=str(e))

    if not (db_ok and redis_ok):
        raise HTTPException(status_code=503, detail={"status": "degraded", **detail})

    return {"status": "ok", "db": "ok", "redis": "ok"}


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Lightweight liveness probe — no dependency checks."""
    return {"status": "alive"}
