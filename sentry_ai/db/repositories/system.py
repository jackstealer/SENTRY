from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sentry_ai.db.models import LLMUsage, ProactiveRun
from sentry_ai.db.repositories.base import Repo
from sentry_ai.db.schemas import LLMUsageCreate, LLMUsageDTO


class SystemRepo(Repo):
    """Process-wide tables (no user_id): proactive-send idempotency and LLM cost."""

    async def claim_proactive_run(self, job_key: str, local_date: date) -> bool:
        """True exactly once per (job, local day): the guard against duplicate briefs."""
        stmt = (
            pg_insert(ProactiveRun)
            .values(job_key=job_key, local_date=local_date)
            .on_conflict_do_nothing()
            .returning(ProactiveRun.job_key)
        )
        return await self.session.scalar(stmt) is not None

    async def record_llm_usage(self, data: LLMUsageCreate) -> LLMUsageDTO:
        stmt = sa.insert(LLMUsage).values(**data.model_dump())
        return await self._created(stmt.returning(LLMUsage), LLMUsageDTO)

    async def llm_usage_between(self, start: datetime, end: datetime) -> list[LLMUsageDTO]:
        """[start, end), oldest first."""
        stmt = (
            sa.select(LLMUsage)
            .where(LLMUsage.created_at >= start, LLMUsage.created_at < end)
            .order_by(LLMUsage.created_at, LLMUsage.id)
        )
        return await self._many(stmt, LLMUsageDTO)
