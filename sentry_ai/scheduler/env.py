"""Live objects for job functions. Stored jobs are pickled, so they carry only a function path
and plain ids; everything else (channel, DB sessions, clock) is installed here at startup."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sentry_ai.agent.deps import AgentDeps, Sessions
from sentry_ai.interfaces.outbound import OutboundChannel
from sentry_ai.scheduler.reminders import ApsReminderScheduler
from sentry_ai.services.time import now_utc


@dataclass
class JobEnv:
    scheduler: Any  # AsyncIOScheduler (APScheduler 3.x is untyped)
    channel: OutboundChannel
    sessions: Sessions
    user_id: str
    clock: Callable[[], datetime] = now_utc
    graph: Any = None  # the compiled agent graph, for proactive turns

    @property
    def reminders(self) -> ApsReminderScheduler:
        return ApsReminderScheduler(self.scheduler)

    def agent_deps(self) -> AgentDeps:
        return AgentDeps(sessions=self.sessions, reminders=self.reminders, clock=self.clock)


_env: JobEnv | None = None


def install(env: JobEnv | None) -> None:
    global _env
    _env = env


def current() -> JobEnv:
    if _env is None:
        raise RuntimeError("scheduler jobs ran before sentry_ai.scheduler.env.install()")
    return _env
