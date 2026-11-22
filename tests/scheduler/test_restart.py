"""ROADMAP 4.8: jobs survive a restart. Real Postgres job store, real (short) waits."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import ReminderRepo
from sentry_ai.db.schemas import ReminderCreate
from sentry_ai.scheduler import env as job_env
from sentry_ai.scheduler.daily import schedule_daily_jobs
from sentry_ai.scheduler.env import JobEnv
from sentry_ai.scheduler.setup import running
from sentry_ai.services.reminders import ReminderService
from sentry_ai.services.time import now_utc

from .conftest import RecordingChannel


def install(scheduler: AsyncIOScheduler, session: AsyncSession, user_id: str) -> JobEnv:
    @asynccontextmanager
    async def sessions() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    env = JobEnv(
        scheduler=scheduler,
        channel=RecordingChannel(),
        sessions=sessions,
        user_id=user_id,
        clock=now_utc,  # real time: the scheduler fires on the real clock here
    )
    job_env.install(env)
    return env


async def test_a_reminder_scheduled_before_a_restart_still_fires_after_it(
    pg_schedulers: Callable[[], AsyncIOScheduler], session: AsyncSession, user_id: str
) -> None:
    try:
        async with running(pg_schedulers()) as before:
            env = install(before, session, user_id)
            service = ReminderService(session, user_id, env.reminders, now=now_utc())
            reminder = await service.create(
                ReminderCreate(message="Stretch", fire_at=now_utc() + timedelta(seconds=2))
            )
        # the process is gone; a new one starts on the same job store
        async with running(pg_schedulers()) as after:
            env = install(after, session, user_id)
            assert [job.id for job in after.get_jobs()] == [f"reminder:{reminder.id}"]
            channel = env.channel
            assert isinstance(channel, RecordingChannel)
            async with asyncio.timeout(10):
                while not channel.sent:  # noqa: ASYNC110 - polling the fake channel's list
                    await asyncio.sleep(0.1)
        assert channel.sent[0][0] == "⏰ Stretch"
        stored = await ReminderRepo(session, user_id).get(reminder.id)
        assert stored is not None and stored.status == "sent"
    finally:
        job_env.install(None)


async def test_a_restart_keeps_daily_jobs_and_their_next_run(
    pg_schedulers: Callable[[], AsyncIOScheduler],
) -> None:
    prefs = {"wake_time": {"time": "06:30"}}
    async with running(pg_schedulers()) as before:
        schedule_daily_jobs(before, prefs)
        planned = {job.id: job.next_run_time for job in before.get_jobs()}
    async with running(pg_schedulers()) as after:
        assert schedule_daily_jobs(after, prefs) == []  # startup again: nothing re-added
        assert {job.id: job.next_run_time for job in after.get_jobs()} == planned
    assert set(planned) == {
        "morning_brief",
        "evening_review",
        "weekly_review",
        "routine_materialize",
    }
