"""Shared base: one repository per aggregate, scoped to one user, never committing."""

from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import Executable

D = TypeVar("D", bound=BaseModel)

# for UPDATE/DELETE: RETURNING plus populate_existing keeps loaded objects fresh, so the
# in-Python "evaluate" sync (which cannot evaluate now() or subqueries) is not needed
NO_SYNC = {"synchronize_session": False}


class Repo:
    """Callers own the transaction: repositories execute statements but never commit."""

    def __init__(self, session: AsyncSession, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    async def _one(self, stmt: Executable, dto: type[D]) -> D | None:
        # populate_existing: never hand back an identity-map object with stale attributes
        row = await self.session.scalar(stmt.execution_options(populate_existing=True))
        return None if row is None else dto.model_validate(row)

    async def _many(self, stmt: Executable, dto: type[D]) -> list[D]:
        rows = await self.session.scalars(stmt.execution_options(populate_existing=True))
        return [dto.model_validate(row) for row in rows]

    async def _created(self, stmt: Executable, dto: type[D]) -> D:
        created = await self._one(stmt, dto)
        if created is None:  # INSERT ... RETURNING always yields a row
            raise RuntimeError("insert returned no row")
        return created

    async def _affected(self, stmt: Executable) -> bool:
        return await self.session.scalar(stmt.execution_options(**NO_SYNC)) is not None
