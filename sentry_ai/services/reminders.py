"""Reminders: the DB row is the source of truth; a ReminderScheduler keeps the firing job in sync.

Phase 2 ships only the no-op scheduler (rows, no jobs). Phase 4 swaps in APScheduler and, at
startup, schedules every pending row, including the ones written while the no-op was in use.
"""

from typing import Protocol, runtime_checkable

from sentry_ai.db.schemas import ReminderDTO
from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class ReminderScheduler(Protocol):
    async def schedule(self, reminder: ReminderDTO) -> str | None:
        """Add or replace the job for this reminder; returns its job id."""
        ...

    async def cancel(self, reminder_id: int) -> None: ...


class NoopReminderScheduler:
    async def schedule(self, reminder: ReminderDTO) -> str | None:
        log.info("reminder_schedule_deferred", reminder_id=reminder.id, fire_at=reminder.fire_at)
        return None

    async def cancel(self, reminder_id: int) -> None:
        log.info("reminder_cancel_deferred", reminder_id=reminder_id)
