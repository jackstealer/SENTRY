"""Shared fixtures: run real tools through the executor against the test DB."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.schemas import ReminderDTO
from sentry_ai.tools.base import ToolContext, ToolResult
from sentry_ai.tools.executor import execute

# Sun 22 Nov 2026, 12:00 IST. Tests that need another instant pass now=...
NOW = datetime(2026, 11, 22, 6, 30, tzinfo=UTC)


class RecordingScheduler:
    """Fake ReminderScheduler: remembers which jobs exist, like APScheduler would."""

    def __init__(self) -> None:
        self.jobs: dict[int, datetime] = {}
        self.cancelled: list[int] = []

    async def schedule(self, reminder: ReminderDTO) -> str | None:
        self.jobs[reminder.id] = reminder.fire_at
        return f"reminder:{reminder.id}"

    async def cancel(self, reminder_id: int) -> None:
        self.jobs.pop(reminder_id, None)
        self.cancelled.append(reminder_id)


class FakeEmbedder:
    """Deterministic 1536-dim vectors: texts sharing a `topic` word point the same way."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        vec = [0.0] * 1536
        for word in text.lower().split():
            vec[sum(map(ord, word.strip(".,!?"))) % 1536] += 1.0
        return vec


class Call(Protocol):
    def __call__(
        self, name: str, /, *, now: datetime = ..., confirmed: bool = ..., **args: Any
    ) -> Awaitable[ToolResult]: ...


@pytest.fixture
def scheduler() -> RecordingScheduler:
    return RecordingScheduler()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def make_ctx(
    session: AsyncSession, user_id: str, scheduler: RecordingScheduler, embedder: FakeEmbedder
) -> Callable[..., ToolContext]:
    def build(now: datetime = NOW, confirmed: bool = False) -> ToolContext:
        return ToolContext(
            user_id=user_id,
            trace_id="test-trace",
            now=now,
            session=session,
            confirmed=confirmed,
            reminders=scheduler,
            embed=embedder,
        )

    return build


@pytest.fixture
def call(make_ctx: Callable[..., ToolContext]) -> Call:
    async def run(
        name: str, /, *, now: datetime = NOW, confirmed: bool = False, **args: Any
    ) -> ToolResult:
        return await execute(name, args, make_ctx(now=now, confirmed=confirmed))

    return run
