from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.db.session import get_sessionmaker


async def test_engine_targets_the_test_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        name = await conn.scalar(text("SELECT current_database()"))
        version = await conn.scalar(text("SHOW server_version_num"))
    assert str(name).endswith("_test")
    assert int(str(version)) >= 160000


async def test_sessionmaker_runs_queries(engine: AsyncEngine) -> None:
    async with get_sessionmaker()() as session:
        assert await session.scalar(text("SELECT 1")) == 1


async def test_sessions_are_independent(engine: AsyncEngine) -> None:
    maker = get_sessionmaker()
    async with maker() as a, maker() as b:
        pid_a = await a.scalar(text("SELECT pg_backend_pid()"))
        pid_b = await b.scalar(text("SELECT pg_backend_pid()"))
    assert pid_a != pid_b
