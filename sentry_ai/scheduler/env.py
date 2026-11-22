"""Live objects for job functions. Stored jobs are pickled, so they carry only a function path
and plain ids; everything else (channel, DB sessions, clock) is installed here at startup."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sentry_ai.agent import llm
from sentry_ai.agent.deps import AgentDeps, Sessions
from sentry_ai.interfaces.outbound import OutboundChannel
from sentry_ai.scheduler.daily import ApsDailySchedule
from sentry_ai.scheduler.reminders import ApsReminderScheduler
from sentry_ai.services.time import now_utc
from sentry_ai.tools.base import Embedder


@dataclass
class JobEnv:
    scheduler: Any  # AsyncIOScheduler (APScheduler 3.x is untyped)
    channel: OutboundChannel
    sessions: Sessions
    user_id: str
    clock: Callable[[], datetime] = now_utc
    graph: Any = None  # the compiled agent graph, for proactive turns
    embed: Embedder | None = None  # None = llm.embed, looked up when used (tests swap it)

    @property
    def reminders(self) -> ApsReminderScheduler:
        return ApsReminderScheduler(self.scheduler)

    def agent_deps(self) -> AgentDeps:
        return AgentDeps(
            sessions=self.sessions,
            reminders=self.reminders,
            daily=ApsDailySchedule(self.scheduler),
            embed=self.embed or llm.embed,
            clock=self.clock,
        )


_env: JobEnv | None = None


def install(env: JobEnv | None) -> None:
    global _env
    _env = env


def current() -> JobEnv:
    if _env is None:
        raise RuntimeError("scheduler jobs ran before sentry_ai.scheduler.env.install()")
    return _env
