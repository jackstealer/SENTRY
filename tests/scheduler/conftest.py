"""Scheduler fixtures: Postgres job stores on throwaway tables in the test DB."""

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.scheduler.setup import build_scheduler


@pytest.fixture
async def pg_schedulers(engine: AsyncEngine) -> AsyncIterator[Callable[[], AsyncIOScheduler]]:
    """Make schedulers that share one job table (like restarts of the app); dropped after."""
    table = f"apscheduler_test_{uuid.uuid4().hex[:8]}"
    made: list[AsyncIOScheduler] = []

    def make() -> AsyncIOScheduler:
        made.append(build_scheduler(tablename=table))
        return made[-1]

    yield make
    for scheduler in made:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
