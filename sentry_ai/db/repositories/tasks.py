from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.enums import TaskStatus
from sentry_ai.db.models import Task
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import TaskCreate, TaskDTO, TaskMatch, TaskUpdate

OPEN_STATUSES: tuple[TaskStatus, ...] = ("todo", "in_progress")


class TaskRepo(Repo):
    def _mine(self) -> ColumnElement[bool]:
        return Task.user_id == self.user_id

    async def create(self, data: TaskCreate) -> TaskDTO:
        stmt = sa.insert(Task).values(user_id=self.user_id, **data.model_dump())
        return await self._created(stmt.returning(Task), TaskDTO)

    async def get(self, task_id: int) -> TaskDTO | None:
        return await self._one(sa.select(Task).where(Task.id == task_id, self._mine()), TaskDTO)

    async def query(
        self,
        *,
        status: Sequence[TaskStatus] | None = None,
        due_before: datetime | None = None,
        due_after: datetime | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> list[TaskDTO]:
        """Active tasks, most urgent first: priority, then earliest due date (undated last).

        Due filters are [due_after, due_before) and leave out undated tasks.
        """
        stmt = sa.select(Task).where(self._mine(), Task.archived_at.is_(None))
        if status:
            stmt = stmt.where(Task.status.in_(status))
        if due_before is not None:
            stmt = stmt.where(Task.due_at < due_before)
        if due_after is not None:
            stmt = stmt.where(Task.due_at >= due_after)
        if project:
            stmt = stmt.where(sa.func.lower(Task.project) == project.strip().lower())
        stmt = stmt.order_by(Task.priority, Task.due_at.asc().nulls_last(), Task.id).limit(limit)
        return await self._many(stmt, TaskDTO)

    async def update(self, task_id: int, changes: TaskUpdate) -> TaskDTO | None:
        values = changes.changes()
        if not values:
            return await self.get(task_id)
        return await self._update(task_id, **values)

    async def set_status(self, task_id: int, status: TaskStatus, at: datetime) -> TaskDTO | None:
        """`at` becomes completed_at for done; any other status clears it."""
        return await self._update(
            task_id, status=status, completed_at=at if status == "done" else None
        )

    async def increment_rollover(self, task_id: int) -> TaskDTO | None:
        return await self._update(task_id, rolled_over_count=Task.rolled_over_count + 1)

    async def find_similar(
        self,
        text: str,
        *,
        limit: int = 5,
        min_similarity: float = 0.3,
        statuses: Sequence[TaskStatus] = OPEN_STATUSES,
    ) -> list[TaskMatch]:
        """Fuzzy title match (pg_trgm) for 'done with the SE thing'-style references."""
        # word_similarity scores the best-matching part of the title, so short queries still hit
        score = sa.func.greatest(
            sa.func.similarity(Task.title, text), sa.func.word_similarity(text, Task.title)
        ).label("score")
        stmt = (
            sa.select(Task, score)
            .where(
                self._mine(),
                Task.archived_at.is_(None),
                Task.status.in_(statuses),
                score >= min_similarity,
            )
            .order_by(score.desc(), Task.id)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        rows = await self.session.execute(stmt)
        return [
            TaskMatch(task=TaskDTO.model_validate(task), similarity=float(similarity))
            for task, similarity in rows
        ]

    def _done_before(self, before: datetime) -> ColumnElement[bool]:
        return sa.and_(
            self._mine(),
            Task.status == "done",
            Task.completed_at < before,
            Task.archived_at.is_(None),
        )

    async def count_done_before(self, before: datetime) -> int:
        """How many tasks archive_done_before would archive (for its confirmation)."""
        stmt = sa.select(sa.func.count()).select_from(Task).where(self._done_before(before))
        return int(await self.session.scalar(stmt) or 0)

    async def archive_done_before(self, before: datetime) -> int:
        stmt = (
            sa.update(Task)
            .where(self._done_before(before))
            .values(archived_at=sa.func.now())
            .returning(Task.id)
            .execution_options(**NO_SYNC)
        )
        return len((await self.session.scalars(stmt)).all())

    async def _update(self, task_id: int, **values: object) -> TaskDTO | None:
        stmt = (
            sa.update(Task)
            .where(Task.id == task_id, self._mine())
            .values(**values)
            .returning(Task)
            .execution_options(**NO_SYNC)
        )
        return await self._one(stmt, TaskDTO)
