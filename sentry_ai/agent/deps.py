"""Live objects the graph's nodes need. Passed as LangGraph runtime context, never checkpointed."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent import llm
from sentry_ai.db.session import get_sessionmaker
from sentry_ai.services.reminders import NoopReminderScheduler, ReminderScheduler
from sentry_ai.services.time import now_utc
from sentry_ai.tools.base import Embedder, ToolContext

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class AgentDeps:
    # each call gives a session inside a transaction that commits when the block exits; nodes
    # that write (tools, persist) use one block per step, so the DB matches each checkpoint
    sessions: Sessions
    reminders: ReminderScheduler = field(default_factory=NoopReminderScheduler)
    embed: Embedder = llm.embed
    clock: Callable[[], datetime] = now_utc

    def tool_context(
        self, session: AsyncSession, *, user_id: str, trace_id: str, confirmed: bool = False
    ) -> ToolContext:
        return ToolContext(
            user_id=user_id,
            trace_id=trace_id,
            now=self.clock(),
            session=session,
            confirmed=confirmed,
            reminders=self.reminders,
            embed=self.embed,
        )


def default_deps() -> AgentDeps:
    return AgentDeps(sessions=get_sessionmaker().begin)
