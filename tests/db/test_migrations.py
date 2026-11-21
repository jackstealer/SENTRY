import asyncio

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _extensions(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm')")
        )
        return {row[0] for row in rows}


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT max(version_num) FROM alembic_version"))


async def test_head_enables_required_extensions(engine: AsyncEngine) -> None:
    assert await _extensions(engine) == {"vector", "pg_trgm"}
    assert await _revision(engine) == "0001"


async def test_downgrade_to_base_then_upgrade_round_trips(
    engine: AsyncEngine, alembic_cfg: Config
) -> None:
    await asyncio.to_thread(command.downgrade, alembic_cfg, "base")
    assert await _extensions(engine) == set()
    assert await _revision(engine) is None

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    assert await _extensions(engine) == {"vector", "pg_trgm"}
    assert await _revision(engine) == "0001"
