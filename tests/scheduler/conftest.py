"""Scheduler fixtures: Postgres job stores on throwaway tables, and an in-memory scheduler plus
an installed JobEnv for calling job functions directly with a frozen clock."""

import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sentry_ai.interfaces.outbound import Button
from sentry_ai.scheduler import env as job_env
from sentry_ai.scheduler.env import JobEnv
from sentry_ai.scheduler.setup import build_scheduler

NOW = datetime(2026, 11, 23, 4, 30, tzinfo=UTC)  # Mon 23 Nov, 10:00 IST


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


class RecordingChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[str]]] = []

    async def send(self, text: str, buttons: Sequence[Button] | None = None) -> int | None:
        self.sent.append((text, [b.action for b in buttons or []]))
        return 1000 + len(self.sent)


@pytest.fixture
async def memory_scheduler() -> AsyncIterator[AsyncIOScheduler]:
    """Started but paused: jobs are stored and can be inspected, nothing fires by itself."""
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.start(paused=True)
    yield scheduler
    scheduler.shutdown(wait=False)


@pytest.fixture
def channel() -> RecordingChannel:
    return RecordingChannel()


@pytest.fixture
def clock() -> list[datetime]:
    """Mutable so a test can move time: clock[0] = ..."""
    return [NOW]


@pytest.fixture
def env(
    session: AsyncSession,
    user_id: str,
    memory_scheduler: AsyncIOScheduler,
    channel: RecordingChannel,
    clock: list[datetime],
) -> Iterator[JobEnv]:
    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():  # "commit" = release a savepoint; the test rolls back
            yield session

    installed = JobEnv(
        scheduler=memory_scheduler,
        channel=channel,
        sessions=sessions,
        user_id=user_id,
        clock=lambda: clock[0],
    )
    job_env.install(installed)
    yield installed
    job_env.install(None)
