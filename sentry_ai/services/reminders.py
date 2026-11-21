"""Reminders: the DB row is the source of truth; a ReminderScheduler keeps the firing job in sync.

Phase 2 ships only the no-op scheduler (rows, no jobs). Phase 4 swaps in APScheduler and, at
startup, schedules every pending row, including the ones written while the no-op was in use.
"""

from datetime import datetime, timedelta
from typing import Literal, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.enums import ReminderTarget
from sentry_ai.db.repositories import HabitRepo, PlanRepo, ReminderRepo, TaskRepo
from sentry_ai.db.repositories.reminders import PENDING_STATUSES
from sentry_ai.db.schemas import PlanBlockUpdate, ReminderCreate, ReminderDTO, ReminderUpdate
from sentry_ai.observability.logging import get_logger
from sentry_ai.services.time import local_date_of

log = get_logger(__name__)

AckAction = Literal["done", "skip"]


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


class ReminderError(ValueError):
    """An expected problem (past time, unknown id); the message is fit to show the user."""


class ReminderService:
    """Every reminder change goes through here so the row and its job never disagree."""

    def __init__(
        self, session: AsyncSession, user_id: str, scheduler: ReminderScheduler, *, now: datetime
    ) -> None:
        self.session, self.user_id, self.scheduler, self.now = session, user_id, scheduler, now
        self.repo = ReminderRepo(session, user_id)

    async def create(self, data: ReminderCreate) -> ReminderDTO:
        if data.fire_at <= self.now:
            raise ReminderError("That time has already passed; pick a time in the future.")
        return await self._schedule(await self.repo.create(data))

    async def snooze(self, reminder_id: int, minutes: int) -> ReminderDTO:
        reminder = await self._get(reminder_id)
        if reminder.status not in (*PENDING_STATUSES, "sent"):
            raise ReminderError(f"Reminder {reminder_id} is already {reminder.status}.")
        change = ReminderUpdate(
            fire_at=self.now + timedelta(minutes=minutes), status="snoozed", nudge_sent=False
        )
        return await self._schedule(await self._update(reminder_id, change))

    async def cancel(self, reminder_id: int) -> ReminderDTO:
        reminder = await self._get(reminder_id)
        if reminder.status == "cancelled":
            return reminder
        if reminder.status not in (*PENDING_STATUSES, "sent"):
            raise ReminderError(f"Reminder {reminder_id} is already {reminder.status}.")
        await self.scheduler.cancel(reminder_id)
        return await self._update(reminder_id, ReminderUpdate(status="cancelled"))

    async def cancel_for(self, kind: ReminderTarget, target_id: int) -> int:
        """Cancel every pending reminder of a task/habit/block; returns how many."""
        pending = await self.repo.for_target(kind, target_id, PENDING_STATUSES)
        for reminder in pending:
            await self.cancel(reminder.id)
        return len(pending)

    async def shift_for(self, kind: ReminderTarget, target_id: int, delta: timedelta) -> int:
        """Move pending reminders with their target; any that would land in the past are dropped."""
        pending = await self.repo.for_target(kind, target_id, PENDING_STATUSES)
        for reminder in pending:
            fire_at = reminder.fire_at + delta
            if fire_at <= self.now:
                await self.cancel(reminder.id)
            else:
                await self._schedule(
                    await self._update(reminder.id, ReminderUpdate(fire_at=fire_at))
                )
        return len(pending)

    async def ack(self, reminder_id: int, action: AckAction) -> tuple[ReminderDTO, str | None]:
        """Done/skip from the reminder's buttons; also completes or skips what it points at."""
        reminder = await self._get(reminder_id)
        if reminder.status not in (*PENDING_STATUSES, "sent"):
            raise ReminderError(f"Reminder {reminder_id} is already {reminder.status}.")
        await self.scheduler.cancel(reminder_id)  # drops a pending nudge job too
        status = "acked" if action == "done" else "skipped"
        updated = await self._update(reminder_id, ReminderUpdate(status=status))
        return updated, await self._apply(reminder.target_kind, reminder.target_id, action)

    async def _apply(
        self, kind: ReminderTarget, target_id: int | None, action: AckAction
    ) -> str | None:
        if target_id is None:
            return None
        # "done"/"skipped" is valid for task status, habit log status and block outcome alike
        status: Literal["done", "skipped"] = "done" if action == "done" else "skipped"
        if kind == "task":
            task = await TaskRepo(self.session, self.user_id).set_status(
                target_id, status, self.now
            )
            if task is None:
                return None
            await self.cancel_for("task", target_id)
            return f"task '{task.title}' marked {status}"
        if kind == "habit":
            habits = HabitRepo(self.session, self.user_id)
            logged = await habits.log(target_id, local_date_of(self.now), status)
            return None if logged is None else f"habit logged {status} for {logged.local_date}"
        if kind == "plan_block":
            plans = PlanRepo(self.session, self.user_id)
            block = await plans.update_block(target_id, PlanBlockUpdate(outcome=status))
            if block is not None and block.ref_id is not None:
                if block.kind == "task":
                    return await self._apply("task", block.ref_id, action)
                if block.kind == "habit":
                    return await self._apply("habit", block.ref_id, action)
        return None

    async def _schedule(self, reminder: ReminderDTO) -> ReminderDTO:
        job_id = await self.scheduler.schedule(reminder)
        if job_id == reminder.job_id:
            return reminder
        return await self._update(reminder.id, ReminderUpdate(job_id=job_id))

    async def _get(self, reminder_id: int) -> ReminderDTO:
        reminder = await self.repo.get(reminder_id)
        if reminder is None:
            raise ReminderError(f"There is no reminder {reminder_id}.")
        return reminder

    async def _update(self, reminder_id: int, change: ReminderUpdate) -> ReminderDTO:
        updated = await self.repo.update(reminder_id, change)
        if updated is None:  # _get already proved it exists and is ours
            raise RuntimeError(f"reminder {reminder_id} vanished during update")
        return updated
