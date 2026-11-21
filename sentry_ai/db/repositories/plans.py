from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from sentry_ai.db.models import DayPlan, PlanBlock
from sentry_ai.db.repositories.base import NO_SYNC, Repo
from sentry_ai.db.schemas import DayPlanDTO, PlanBlockCreate, PlanBlockDTO, PlanBlockUpdate


class PlanRepo(Repo):
    def _mine(self) -> ColumnElement[bool]:
        return DayPlan.user_id == self.user_id

    async def create_draft(
        self, local_date: date, summary: str | None, blocks: Sequence[PlanBlockCreate]
    ) -> DayPlanDTO:
        plan_id = await self.session.scalar(
            sa.insert(DayPlan)
            .values(user_id=self.user_id, local_date=local_date, summary=summary)
            .returning(DayPlan.id)
        )
        if plan_id is None:  # INSERT ... RETURNING always yields a row
            raise RuntimeError("insert returned no row")
        if blocks:
            await self.session.execute(
                sa.insert(PlanBlock), [b.model_dump() | {"plan_id": plan_id} for b in blocks]
            )
        created = await self.get(plan_id)
        if created is None:
            raise RuntimeError("draft plan vanished after insert")
        return created

    async def get(self, plan_id: int) -> DayPlanDTO | None:
        plan = await self._one(
            sa.select(DayPlan).where(DayPlan.id == plan_id, self._mine()), DayPlanDTO
        )
        if plan is None:
            return None
        blocks = await self._many(
            sa.select(PlanBlock)
            .where(PlanBlock.plan_id == plan_id)
            .order_by(PlanBlock.start_at, PlanBlock.id),
            PlanBlockDTO,
        )
        return plan.model_copy(update={"blocks": blocks})

    async def get_confirmed(self, local_date: date) -> DayPlanDTO | None:
        return await self._get_first(
            sa.select(DayPlan.id).where(
                self._mine(), DayPlan.local_date == local_date, DayPlan.status == "confirmed"
            )
        )

    async def latest_for(self, local_date: date) -> DayPlanDTO | None:
        """Newest plan for the day that is not superseded (a draft or the confirmed one)."""
        return await self._get_first(
            sa.select(DayPlan.id)
            .where(self._mine(), DayPlan.local_date == local_date, DayPlan.status != "superseded")
            .order_by(DayPlan.id.desc())
        )

    async def confirm(self, plan_id: int) -> DayPlanDTO | None:
        """Confirm one plan and supersede every other live plan for that day, atomically."""
        local_date = await self.session.scalar(
            sa.select(DayPlan.local_date).where(DayPlan.id == plan_id, self._mine())
        )
        if local_date is None:
            return None
        # supersede first: uq_day_plans_confirmed allows one confirmed plan per day
        await self.session.execute(
            sa.update(DayPlan)
            .where(
                self._mine(),
                DayPlan.local_date == local_date,
                DayPlan.id != plan_id,
                DayPlan.status != "superseded",
            )
            .values(status="superseded")
            .execution_options(**NO_SYNC)
        )
        await self.session.execute(
            sa.update(DayPlan)
            .where(DayPlan.id == plan_id)
            .values(status="confirmed")
            .execution_options(**NO_SYNC)
        )
        return await self.get(plan_id)

    async def update_block(self, block_id: int, changes: PlanBlockUpdate) -> PlanBlockDTO | None:
        values = changes.changes()
        owned = PlanBlock.plan_id.in_(sa.select(DayPlan.id).where(self._mine()))
        if not values:
            return await self._one(
                sa.select(PlanBlock).where(PlanBlock.id == block_id, owned), PlanBlockDTO
            )
        stmt = (
            sa.update(PlanBlock)
            .where(PlanBlock.id == block_id, owned)
            .values(**values)
            .returning(PlanBlock)
            .execution_options(**NO_SYNC)
        )
        return await self._one(stmt, PlanBlockDTO)

    async def _get_first(self, ids: sa.Select[int]) -> DayPlanDTO | None:
        plan_id = await self.session.scalar(ids.limit(1))
        return None if plan_id is None else await self.get(plan_id)
