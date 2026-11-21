from datetime import date

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.models import Routine
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import RoutineCreate, RoutineDTO, RoutineUpdate


class RoutineRepo(Repo):
    def _mine(self) -> ColumnElement[bool]:
        return Routine.user_id == self.user_id

    async def create(self, data: RoutineCreate) -> RoutineDTO:
        stmt = sa.insert(Routine).values(user_id=self.user_id, **data.model_dump())
        return await self._created(stmt.returning(Routine), RoutineDTO)

    async def get(self, routine_id: int) -> RoutineDTO | None:
        stmt = sa.select(Routine).where(Routine.id == routine_id, self._mine())
        return await self._one(stmt, RoutineDTO)

    async def get_by_title(self, title: str) -> RoutineDTO | None:
        """Case-insensitive, active rows only: the seed's natural key."""
        stmt = (
            sa.select(Routine)
            .where(
                self._mine(),
                Routine.archived_at.is_(None),
                sa.func.lower(Routine.title) == title.strip().lower(),
            )
            .order_by(Routine.id)
            .limit(1)
        )
        return await self._one(stmt, RoutineDTO)

    async def list_active(self, on: date | None = None) -> list[RoutineDTO]:
        """Not archived; with `on`, only routines whose active window includes that date."""
        stmt = sa.select(Routine).where(self._mine(), Routine.archived_at.is_(None))
        if on is not None:
            stmt = stmt.where(
                sa.or_(Routine.active_from.is_(None), Routine.active_from <= on),
                sa.or_(Routine.active_until.is_(None), Routine.active_until >= on),
            )
        return await self._many(stmt.order_by(Routine.start_time, Routine.id), RoutineDTO)

    async def update(self, routine_id: int, changes: RoutineUpdate) -> RoutineDTO | None:
        values = changes.changes()
        if not values:
            return await self.get(routine_id)
        stmt = (
            sa.update(Routine)
            .where(Routine.id == routine_id, self._mine())
            .values(**values)
            .returning(Routine)
            .execution_options(**NO_SYNC)
        )
        return await self._one(stmt, RoutineDTO)

    async def archive(self, routine_id: int) -> bool:
        stmt = (
            sa.update(Routine)
            .where(Routine.id == routine_id, self._mine(), Routine.archived_at.is_(None))
            .values(archived_at=sa.func.now())
            .returning(Routine.id)
        )
        return await self._affected(stmt)

    async def delete(self, routine_id: int) -> bool:
        stmt = sa.delete(Routine).where(Routine.id == routine_id, self._mine())
        return await self._affected(stmt.returning(Routine.id))
