from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from astrobot.config import get_settings


@lru_cache
def get_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        # A connection is held for the whole duration of each update (incl. the
        # multi-second LLM call), so the pool must cover peak concurrent users.
        # 30 + 30 = 60 max, comfortably under Postgres' default max_connections=100.
        pool_size=30,
        max_overflow=30,
        pool_timeout=30,
        pool_recycle=1800,
        future=True,
        connect_args={
            "timeout": 10,
            "command_timeout": 30,
            "server_settings": {
                "statement_timeout": "30000",
                "application_name": "astrobot",
            },
        },
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
