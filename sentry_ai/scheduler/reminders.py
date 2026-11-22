"""Reminder jobs in APScheduler, kept in step with `reminders` rows (ARCHITECTURE section 5).

ReminderService calls `schedule` / `cancel` in the same call that changes the row. The job
store commits on its own connection, so a rolled-back turn can leave an orphan job, and a
crash can leave a row without a job: `fire_reminder` re-checks the row, and `reconcile` at
startup repairs both directions.
"""

from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.date import DateTrigger

from sentry_ai.db.schemas import ReminderDTO
from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)

REMINDER_JOB = "sentry_ai.scheduler.reminder_jobs:fire_reminder"
NUDGE_JOB = "sentry_ai.scheduler.reminder_jobs:fire_nudge"
REMINDER_GRACE_S = 5 * 60  # ARCHITECTURE: 5 min for reminders; later ones go via reconcile
NUDGE_AFTER = timedelta(minutes=15)


def reminder_job_id(reminder_id: int) -> str:
    return f"reminder:{reminder_id}"


def nudge_job_id(reminder_id: int) -> str:
    return f"nudge:{reminder_id}"


class ApsReminderScheduler:
    """The real ReminderScheduler (services.reminders) for the running app."""

    def __init__(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    async def schedule(self, reminder: ReminderDTO) -> str | None:
        job_id = reminder_job_id(reminder.id)
        self._add(REMINDER_JOB, job_id, reminder.id, reminder.fire_at)
        self._remove(nudge_job_id(reminder.id))  # a moved or snoozed reminder starts fresh
        log.info("reminder_scheduled", reminder_id=reminder.id, fire_at=reminder.fire_at)
        return job_id

    async def cancel(self, reminder_id: int) -> None:
        self._remove(reminder_job_id(reminder_id))
        self._remove(nudge_job_id(reminder_id))
        log.info("reminder_unscheduled", reminder_id=reminder_id)

    def schedule_nudge(self, reminder_id: int, at: datetime) -> None:
        self._add(NUDGE_JOB, nudge_job_id(reminder_id), reminder_id, at)

    def _add(self, func: str, job_id: str, reminder_id: int, at: datetime) -> None:
        self.scheduler.add_job(
            func,
            DateTrigger(run_date=at),
            args=[reminder_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=REMINDER_GRACE_S,
        )

    def _remove(self, job_id: str) -> None:
        with suppress(JobLookupError):  # already gone (fired, or never scheduled)
            self.scheduler.remove_job(job_id)
