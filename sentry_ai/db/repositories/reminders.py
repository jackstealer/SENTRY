from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.enums import ReminderStatus, ReminderTarget
from sentry_ai.db.models import Reminder
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import ReminderCreate, ReminderDTO, ReminderUpdate

PENDING_STATUSES: tuple[ReminderStatus, ...] = ("scheduled", "snoozed")


class ReminderRepo(Repo):
    def _mine(self) -> ColumnElement[bool]:
        return Reminder.user_id == self.user_id

    async def create(self, data: ReminderCreate) -> ReminderDTO:
        stmt = sa.insert(Reminder).values(user_id=self.user_id, **data.model_dump())
        return await self._created(stmt.returning(Reminder), ReminderDTO)

    async def get(self, reminder_id: int) -> ReminderDTO | None:
        stmt = sa.select(Reminder).where(Reminder.id == reminder_id, self._mine())
        return await self._one(stmt, ReminderDTO)

    async def list_pending(self, before: datetime | None = None) -> list[ReminderDTO]:
        """Scheduled or snoozed, soonest first; `before` limits to fire_at <= before."""
        stmt = sa.select(Reminder).where(self._mine(), Reminder.status.in_(PENDING_STATUSES))
        if before is not None:
            stmt = stmt.where(Reminder.fire_at <= before)
        return await self._many(stmt.order_by(Reminder.fire_at, Reminder.id), ReminderDTO)

    async def for_target(
        self,
        kind: ReminderTarget,
        target_id: int,
        statuses: Sequence[ReminderStatus] | None = None,
    ) -> list[ReminderDTO]:
        stmt = sa.select(Reminder).where(
            self._mine(), Reminder.target_kind == kind, Reminder.target_id == target_id
        )
        if statuses:
            stmt = stmt.where(Reminder.status.in_(statuses))
        return await self._many(stmt.order_by(Reminder.fire_at, Reminder.id), ReminderDTO)

    async def update(self, reminder_id: int, changes: ReminderUpdate) -> ReminderDTO | None:
        values = changes.changes()
        if not values:
            return await self.get(reminder_id)
        stmt = (
            sa.update(Reminder)
            .where(Reminder.id == reminder_id, self._mine())
            .values(**values)
            .returning(Reminder)
            .execution_options(**NO_SYNC)
        )
        return await self._one(stmt, ReminderDTO)
