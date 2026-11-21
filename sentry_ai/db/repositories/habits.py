from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.enums import HabitLogStatus
from sentry_ai.db.models import Habit, HabitLog
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import HabitCreate, HabitDTO, HabitLogDTO, HabitUpdate


class HabitRepo(Repo):
    def _mine(self) -> ColumnElement[bool]:
        return Habit.user_id == self.user_id

    async def create(self, data: HabitCreate) -> HabitDTO:
        stmt = sa.insert(Habit).values(user_id=self.user_id, **data.model_dump())
        return await self._created(stmt.returning(Habit), HabitDTO)

    async def get(self, habit_id: int) -> HabitDTO | None:
        return await self._one(sa.select(Habit).where(Habit.id == habit_id, self._mine()), HabitDTO)

    async def get_by_name(self, name: str) -> HabitDTO | None:
        """Case-insensitive, active rows only: used by log_habit(habit_name=...) and the seed."""
        stmt = (
            sa.select(Habit)
            .where(
                self._mine(),
                Habit.archived_at.is_(None),
                sa.func.lower(Habit.name) == name.strip().lower(),
            )
            .order_by(Habit.id)
            .limit(1)
        )
        return await self._one(stmt, HabitDTO)

    async def list_active(self) -> list[HabitDTO]:
        stmt = sa.select(Habit).where(self._mine(), Habit.archived_at.is_(None)).order_by(Habit.id)
        return await self._many(stmt, HabitDTO)

    async def update(self, habit_id: int, changes: HabitUpdate) -> HabitDTO | None:
        values = changes.changes()
        if not values:
            return await self.get(habit_id)
        stmt = (
            sa.update(Habit)
            .where(Habit.id == habit_id, self._mine())
            .values(**values)
            .returning(Habit)
            .execution_options(**NO_SYNC)
        )
        return await self._one(stmt, HabitDTO)

    async def archive(self, habit_id: int) -> bool:
        stmt = (
            sa.update(Habit)
            .where(Habit.id == habit_id, self._mine(), Habit.archived_at.is_(None))
            .values(archived_at=sa.func.now())
            .returning(Habit.id)
        )
        return await self._affected(stmt)

    async def delete(self, habit_id: int) -> bool:
        """Hard delete, logs included (habit_logs has no ON DELETE CASCADE)."""
        if await self.get(habit_id) is None:
            return False
        await self.session.execute(
            sa.delete(HabitLog).where(HabitLog.habit_id == habit_id).execution_options(**NO_SYNC)
        )
        return await self._affected(
            sa.delete(Habit).where(Habit.id == habit_id, self._mine()).returning(Habit.id)
        )

    async def log(
        self, habit_id: int, local_date: date, status: HabitLogStatus, note: str | None = None
    ) -> HabitLogDTO | None:
        """One row per habit per local day: logging again replaces that day's status."""
        if await self.get(habit_id) is None:
            return None
        stmt = pg_insert(HabitLog).values(
            user_id=self.user_id,
            habit_id=habit_id,
            local_date=local_date,
            status=status,
            note=note,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[HabitLog.habit_id, HabitLog.local_date],
            set_={
                "status": stmt.excluded.status,
                "note": stmt.excluded.note,
                "logged_at": sa.func.now(),
            },
        )
        return await self._one(stmt.returning(HabitLog), HabitLogDTO)

    async def logs_between(self, habit_id: int, start: date, end: date) -> list[HabitLogDTO]:
        """Inclusive on both ends; dates are local (USER_TZ) days."""
        stmt = (
            sa.select(HabitLog)
            .where(
                HabitLog.habit_id == habit_id,
                HabitLog.user_id == self.user_id,
                HabitLog.local_date.between(start, end),
            )
            .order_by(HabitLog.local_date)
        )
        return await self._many(stmt, HabitLogDTO)
