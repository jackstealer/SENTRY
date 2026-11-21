"""One async engine per process and the session factory built on it."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sentry_ai.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    # pre_ping: survive Postgres restarts (laptop sleep, container recreate) without errors
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: repositories return DTOs built after commit
    return async_sessionmaker(get_engine(), expire_on_commit=False)
