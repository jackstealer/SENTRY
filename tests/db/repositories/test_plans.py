from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.models import DayPlan
from sentry_ai.db.repositories import PlanRepo
from sentry_ai.db.schemas import PlanBlockCreate, PlanBlockUpdate

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 22)


def block(hour: int, title: str, minutes: int = 60) -> PlanBlockCreate:
    start = datetime(2026, 11, 22, hour, 0, tzinfo=IST)
    return PlanBlockCreate(
        start_at=start, end_at=start + timedelta(minutes=minutes), kind="task", title=title
    )


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> PlanRepo:
    return PlanRepo(session, user_id)


async def test_create_draft_with_blocks_sorted_by_start(repo: PlanRepo) -> None:
    plan = await repo.create_draft(DAY, "Light Sunday", [block(21, "DSA"), block(10, "Laundry")])
    assert (plan.status, plan.local_date, plan.summary) == ("draft", DAY, "Light Sunday")
    assert [b.title for b in plan.blocks] == ["Laundry", "DSA"]
    assert all(b.plan_id == plan.id for b in plan.blocks)
    assert await repo.get(plan.id) == plan

    empty = await repo.create_draft(DAY, None, [])
    assert empty.blocks == []


async def test_plans_are_scoped_to_the_user(repo: PlanRepo, session: AsyncSession) -> None:
    plan = await repo.create_draft(DAY, None, [block(10, "Laundry")])
    other = PlanRepo(session, "someone-else")
    assert await other.get(plan.id) is None
    assert await other.confirm(plan.id) is None
    assert await other.update_block(plan.blocks[0].id, PlanBlockUpdate(outcome="done")) is None
    assert await other.latest_for(DAY) is None


async def test_confirm_supersedes_other_plans_for_the_day(repo: PlanRepo) -> None:
    first = await repo.create_draft(DAY, "v1", [])
    second = await repo.create_draft(DAY, "v2", [])
    other_day = await repo.create_draft(DAY + timedelta(days=1), "tomorrow", [])

    confirmed = await repo.confirm(first.id)
    assert confirmed is not None and confirmed.status == "confirmed"
    assert (await repo.get_confirmed(DAY)) == confirmed

    reconfirmed = await repo.confirm(second.id)
    assert reconfirmed is not None and reconfirmed.status == "confirmed"
    old = await repo.get(first.id)
    assert old is not None and old.status == "superseded"
    untouched = await repo.get(other_day.id)
    assert untouched is not None and untouched.status == "draft"
    assert await repo.confirm(999_999) is None


async def test_latest_for_skips_superseded(repo: PlanRepo) -> None:
    assert await repo.latest_for(DAY) is None
    first = await repo.create_draft(DAY, "v1", [])
    await repo.confirm(first.id)
    assert (await repo.latest_for(DAY)).id == first.id  # type: ignore[union-attr]
    newer = await repo.create_draft(DAY, "v2", [])
    assert (await repo.latest_for(DAY)).id == newer.id  # type: ignore[union-attr]
    await repo.confirm(newer.id)
    assert (await repo.get_confirmed(DAY)).id == newer.id  # type: ignore[union-attr]


async def test_db_allows_only_one_confirmed_plan_per_day(
    repo: PlanRepo, session: AsyncSession
) -> None:
    a = await repo.create_draft(DAY, "a", [])
    b = await repo.create_draft(DAY, "b", [])
    await repo.confirm(a.id)
    with pytest.raises(IntegrityError, match="uq_day_plans_confirmed"):
        async with session.begin_nested():  # bypass the repo: hit the partial unique index
            await session.execute(
                sa.update(DayPlan).where(DayPlan.id == b.id).values(status="confirmed")
            )


async def test_update_block_moves_and_records_outcome(repo: PlanRepo) -> None:
    plan = await repo.create_draft(DAY, None, [block(10, "Laundry")])
    original = plan.blocks[0]
    later = original.start_at + timedelta(hours=2)
    moved = await repo.update_block(
        original.id, PlanBlockUpdate(start_at=later, end_at=later + timedelta(hours=1))
    )
    assert moved is not None and moved.start_at == later
    tagged = await repo.update_block(
        original.id, PlanBlockUpdate(outcome="done", gcal_event_id="gcal-1")
    )
    assert tagged is not None and (tagged.outcome, tagged.gcal_event_id) == ("done", "gcal-1")
    assert await repo.update_block(original.id, PlanBlockUpdate()) == tagged
    assert await repo.update_block(999_999, PlanBlockUpdate(outcome="done")) is None


async def test_get_block_is_scoped_through_the_plan(repo: PlanRepo, session: AsyncSession) -> None:
    plan = await repo.create_draft(DAY, None, [block(10, "Laundry")])
    [laundry] = plan.blocks
    assert await repo.get_block(laundry.id) == laundry
    assert await PlanRepo(session, "someone-else").get_block(laundry.id) is None
    assert await repo.get_block(999_999) is None
