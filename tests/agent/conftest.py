"""Agent test fixtures: a scripted fake LLM and AgentDeps bound to the rolled-back test session."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent import llm
from sentry_ai.agent.deps import AgentDeps

from ..fakes import FakeLLM, completion, fake_embed

__all__ = ["NOW", "FakeLLM", "completion", "fake_embed"]

NOW = datetime(2026, 11, 23, 4, 30, tzinfo=UTC)  # Mon 23 Nov, 10:00 IST


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(llm, "chat", fake)
    return fake


@pytest.fixture
def deps(session: AsyncSession) -> AgentDeps:
    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():  # "commit" = release a savepoint; the test rolls back
            yield session

    return AgentDeps(sessions=sessions, embed=fake_embed, clock=lambda: NOW)
