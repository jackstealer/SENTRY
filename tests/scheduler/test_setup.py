from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from sentry_ai.scheduler.setup import JOB_DEFAULTS, jobstore_url, running


async def noop() -> None:
    """Stand-in job target."""


def test_job_store_uses_the_sync_psycopg_driver_on_the_same_database() -> None:
    url = jobstore_url("postgresql+asyncpg://sentry:secret@127.0.0.1:5432/sentry_test")
    assert url == "postgresql+psycopg://sentry:secret@127.0.0.1:5432/sentry_test"


async def test_scheduler_runs_in_user_tz_with_safe_defaults(
    pg_schedulers: Callable[[], AsyncIOScheduler],
) -> None:
    scheduler = pg_schedulers()
    assert str(scheduler.timezone) == "Asia/Kolkata"
    assert JOB_DEFAULTS == {"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
    async with running(scheduler):
        assert scheduler.running
        job = scheduler.add_job(
            f"{__name__}:noop", DateTrigger(datetime.now(UTC) + timedelta(hours=1)), id="probe"
        )
        assert (job.coalesce, job.max_instances, job.misfire_grace_time) == (True, 1, 300)
    assert not scheduler.running


async def test_jobs_are_stored_in_postgres_not_in_memory(
    pg_schedulers: Callable[[], AsyncIOScheduler],
) -> None:
    async with running(pg_schedulers()) as first:
        first.add_job(
            f"{__name__}:noop", DateTrigger(datetime.now(UTC) + timedelta(hours=1)), id="kept"
        )
    async with running(pg_schedulers()) as second:  # a fresh scheduler on the same table
        assert [job.id for job in second.get_jobs()] == ["kept"]


async def test_start_jobs_installs_the_env_and_reconciles(
    pg_schedulers: Callable[[], AsyncIOScheduler],
) -> None:
    from sentry_ai.interfaces.outbound import LogChannel
    from sentry_ai.scheduler import env as job_env
    from sentry_ai.scheduler.startup import start_jobs

    async with running(pg_schedulers()) as scheduler:
        installed = await start_jobs(scheduler)
        try:
            assert job_env.current() is installed
            assert isinstance(installed.channel, LogChannel)
            assert installed.reminders.scheduler is scheduler
            assert {"morning_brief", "evening_review", "weekly_review", "routine_materialize"} <= {
                job.id for job in scheduler.get_jobs()
            }
        finally:
            job_env.install(None)
