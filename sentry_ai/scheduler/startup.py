"""What the running app does once the scheduler is up: install the job environment, put the
daily jobs at the preferred times, repair reminders and top up today's routine reminders."""

from typing import Any

from sentry_ai.config import get_settings
from sentry_ai.db.repositories import PreferenceRepo
from sentry_ai.db.session import get_sessionmaker
from sentry_ai.interfaces.outbound import LogChannel, OutboundChannel
from sentry_ai.observability.logging import bind_trace
from sentry_ai.scheduler import env as job_env
from sentry_ai.scheduler.env import JobEnv
from sentry_ai.scheduler.jobs import routine_materialize, schedule_daily_jobs
from sentry_ai.scheduler.reminder_jobs import reconcile


async def start_jobs(
    scheduler: Any, channel: OutboundChannel | None = None, graph: Any = None
) -> JobEnv:
    bind_trace(job="startup")
    env = JobEnv(
        scheduler=scheduler,
        channel=channel or LogChannel(),
        sessions=get_sessionmaker().begin,
        user_id=get_settings().user_id,
        graph=graph,
    )
    job_env.install(env)
    async with env.sessions() as session:
        prefs = await PreferenceRepo(session, env.user_id).all()
    schedule_daily_jobs(scheduler, prefs)
    await reconcile(env)
    await routine_materialize()  # today's reminders, in case the app was off at 00:05
    return env
