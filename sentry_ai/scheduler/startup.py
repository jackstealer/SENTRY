"""What the running app does once the scheduler is up: install the job environment, then
repair reminders (and, from 4.3/4.6, register the daily jobs at the preferred times)."""

from typing import Any

from sentry_ai.config import get_settings
from sentry_ai.db.session import get_sessionmaker
from sentry_ai.interfaces.outbound import LogChannel, OutboundChannel
from sentry_ai.observability.logging import bind_trace
from sentry_ai.scheduler import env as job_env
from sentry_ai.scheduler.env import JobEnv
from sentry_ai.scheduler.reminder_jobs import reconcile


async def start_jobs(scheduler: Any, channel: OutboundChannel | None = None) -> JobEnv:
    bind_trace(job="startup")
    env = JobEnv(
        scheduler=scheduler,
        channel=channel or LogChannel(),
        sessions=get_sessionmaker().begin,
        user_id=get_settings().user_id,
    )
    job_env.install(env)
    await reconcile(env)
    return env
