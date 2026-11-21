"""Points the whole test run at `<dev db>_test` so tests can never touch real data."""

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from sentry_ai.config import Settings, get_settings
from sentry_ai.db.session import get_engine


def _test_database_url() -> str:
    url = make_url(os.environ.get("DATABASE_URL") or Settings().database_url)
    if not (url.database or "").endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    return url.render_as_string(hide_password=False)


# Must run before anything calls get_settings(): every cached engine then targets the test DB.
TEST_DATABASE_URL = _test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
get_settings.cache_clear()
get_engine.cache_clear()


async def _create_database_if_missing(url: str) -> None:
    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await admin.dispose()


@pytest.fixture(scope="session")
async def database() -> str:
    await _create_database_if_missing(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def engine(database: str) -> AsyncIterator[AsyncEngine]:
    eng = get_engine()
    yield eng
    await eng.dispose()
