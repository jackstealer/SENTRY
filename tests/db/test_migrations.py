import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.db.models import NOT_OURS, Base, include_name

PHASE_1_TABLES = set(Base.metadata.tables)


async def _extensions(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')")
        )
        return {row[0] for row in rows}


async def _tables(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        return {row[0] for row in rows} - {"alembic_version"} - NOT_OURS


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT max(version_num) FROM alembic_version"))


async def _migrate(engine: AsyncEngine, cfg: Config, direction: str, target: str) -> None:
    # env.py runs asyncio.run(), so it needs a thread with no running loop
    await asyncio.to_thread(getattr(command, direction), cfg, target)
    # drop pooled connections: asyncpg caches statements for tables that may be gone
    await engine.dispose()


def _head(cfg: Config) -> str:
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


async def test_head_has_extensions_and_every_phase_1_table(
    engine: AsyncEngine, alembic_cfg: Config
) -> None:
    assert await _revision(engine) == _head(alembic_cfg)
    assert await _extensions(engine) == {"vector", "pg_trgm"}
    assert await _tables(engine) == PHASE_1_TABLES


async def test_models_match_migrations(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync: compare_metadata(
                MigrationContext.configure(sync, opts={"include_name": include_name}),
                Base.metadata,
            )
        )
    assert diff == []


async def test_step_down_one_revision_and_back(engine: AsyncEngine, alembic_cfg: Config) -> None:
    await _migrate(engine, alembic_cfg, "downgrade", "-1")
    assert await _tables(engine) == set()
    assert await _extensions(engine) == {"vector", "pg_trgm"}  # 0001 still applied

    await _migrate(engine, alembic_cfg, "upgrade", "head")
    assert await _tables(engine) == PHASE_1_TABLES


async def test_downgrade_to_base_then_upgrade_round_trips(
    engine: AsyncEngine, alembic_cfg: Config
) -> None:
    await _migrate(engine, alembic_cfg, "downgrade", "base")
    assert await _extensions(engine) == set()
    assert await _tables(engine) == set()
    assert await _revision(engine) is None

    await _migrate(engine, alembic_cfg, "upgrade", "head")
    assert await _extensions(engine) == {"vector", "pg_trgm"}
    assert await _tables(engine) == PHASE_1_TABLES
    assert await _revision(engine) == _head(alembic_cfg)
