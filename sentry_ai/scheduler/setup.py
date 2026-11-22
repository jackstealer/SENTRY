"""APScheduler setup (ARCHITECTURE section 5): jobs live in Postgres, so they survive restarts.

The job store is synchronous (APScheduler 3.x): same database, psycopg instead of asyncpg.
Its writes are small and rare (a reminder created or moved), so they run inline.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.engine import make_url

from sentry_ai.config import get_settings
from sentry_ai.observability.logging import get_logger
from sentry_ai.services.time import user_tz

log = get_logger(__name__)

JOBS_TABLE = "apscheduler_jobs"
# coalesce: runs missed while the app was down collapse into one; one run of a job at a time
JOB_DEFAULTS: dict[str, Any] = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}


def jobstore_url(database_url: str) -> str:
    return (
        make_url(database_url)
        .set(drivername="postgresql+psycopg")
        .render_as_string(hide_password=False)
    )


def build_scheduler(
    *, database_url: str | None = None, tablename: str = JOBS_TABLE
) -> AsyncIOScheduler:
    store = SQLAlchemyJobStore(
        url=jobstore_url(database_url or get_settings().database_url), tablename=tablename
    )
    return AsyncIOScheduler(
        jobstores={"default": store}, job_defaults=JOB_DEFAULTS, timezone=user_tz()
    )


@asynccontextmanager
async def running(scheduler: AsyncIOScheduler) -> AsyncIterator[AsyncIOScheduler]:
    """Start on the current event loop; stop (without waiting for running jobs) on exit."""
    scheduler.start()
    log.info("scheduler_started", jobs=len(scheduler.get_jobs()))
    try:
        yield scheduler
    finally:
        scheduler.shutdown(wait=False)
        await asyncio.sleep(0)  # AsyncIOScheduler stops on the loop's next turn
        log.info("scheduler_stopped")
