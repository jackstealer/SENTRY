from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.enums import EpisodeRole, MemoryKind
from sentry_ai.db.models import EMBEDDING_DIMS, EpisodicEntry, Memory
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import EpisodeDTO, MemoryCreate, MemoryDTO, MemoryMatch


class MemoryRepo(Repo):
    """Episodic log (what happened) and semantic memories (what SENTRY knows)."""

    def _mine(self) -> ColumnElement[bool]:
        return Memory.user_id == self.user_id

    async def log_episode(
        self,
        trace_id: str,
        role: EpisodeRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> EpisodeDTO:
        stmt = sa.insert(EpisodicEntry).values(
            user_id=self.user_id,
            trace_id=trace_id,
            role=role,
            content=content,
            meta=metadata or {},
        )
        return await self._created(stmt.returning(EpisodicEntry), EpisodeDTO)

    async def episodes_between(self, start: datetime, end: datetime) -> list[EpisodeDTO]:
        """[start, end) in UTC; pass services.time day bounds for one local day."""
        stmt = (
            sa.select(EpisodicEntry)
            .where(
                EpisodicEntry.user_id == self.user_id,
                EpisodicEntry.created_at >= start,
                EpisodicEntry.created_at < end,
            )
            .order_by(EpisodicEntry.created_at, EpisodicEntry.id)
        )
        return await self._many(stmt, EpisodeDTO)

    async def add(self, data: MemoryCreate) -> MemoryDTO:
        stmt = sa.insert(Memory).values(user_id=self.user_id, **data.model_dump())
        return await self._created(stmt.returning(Memory), MemoryDTO)

    async def get(self, memory_id: int) -> MemoryDTO | None:
        stmt = sa.select(Memory).where(Memory.id == memory_id, self._mine())
        return await self._one(stmt, MemoryDTO)

    async def search(
        self, embedding: Sequence[float], k: int = 6, kinds: Sequence[MemoryKind] | None = None
    ) -> list[MemoryMatch]:
        """Nearest live (not superseded) memories by cosine distance, closest first."""
        if len(embedding) != EMBEDDING_DIMS:
            raise ValueError(
                f"embedding has {len(embedding)} dims, schema expects {EMBEDDING_DIMS}"
            )
        distance = Memory.embedding.op("<=>", return_type=sa.Float)(list(embedding))
        stmt = sa.select(Memory, distance.label("distance")).where(
            self._mine(), Memory.superseded_by.is_(None)
        )
        if kinds:
            stmt = stmt.where(Memory.kind.in_(kinds))
        stmt = stmt.order_by(distance, Memory.id).limit(k)
        rows = await self.session.execute(stmt.execution_options(populate_existing=True))
        return [
            MemoryMatch(memory=MemoryDTO.model_validate(memory), distance=float(dist))
            for memory, dist in rows
        ]

    async def supersede(self, old_id: int, new_id: int) -> bool:
        stmt = (
            sa.update(Memory)
            .where(Memory.id == old_id, self._mine())
            .values(superseded_by=new_id)
            .returning(Memory.id)
        )
        return await self._affected(stmt)

    async def touch(self, memory_ids: Sequence[int], at: datetime) -> None:
        """Mark memories as used (feeds decay in consolidation)."""
        if memory_ids:
            await self.session.execute(
                sa.update(Memory)
                .where(Memory.id.in_(memory_ids), self._mine())
                .values(last_used_at=at)
                .execution_options(**NO_SYNC)
            )

    async def delete(self, memory_id: int) -> bool:
        # unlink anything superseded by this memory first (self-referencing FK)
        await self.session.execute(
            sa.update(Memory)
            .where(Memory.superseded_by == memory_id, self._mine())
            .values(superseded_by=None)
            .execution_options(**NO_SYNC)
        )
        stmt = sa.delete(Memory).where(Memory.id == memory_id, self._mine())
        return await self._affected(stmt.returning(Memory.id))
