from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import ReminderRepo
from sentry_ai.db.schemas import ReminderCreate, ReminderUpdate

T0 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> ReminderRepo:
    return ReminderRepo(session, user_id)


def at(minutes: int, message: str = "x", **kw: object) -> ReminderCreate:
    return ReminderCreate.model_validate(
        {"message": message, "fire_at": T0 + timedelta(minutes=minutes)} | kw
    )


async def test_create_defaults_and_get(repo: ReminderRepo) -> None:
    reminder = await repo.create(at(0, "Call mom"))
    assert (reminder.status, reminder.target_kind, reminder.nudge_sent) == (
        "scheduled",
        "freeform",
        False,
    )
    assert await repo.get(reminder.id) == reminder


async def test_list_pending_is_soonest_first_and_filters(repo: ReminderRepo) -> None:
    later = await repo.create(at(60, "later"))
    soon = await repo.create(at(10, "soon"))
    acked = await repo.create(at(5, "acked"))
    await repo.update(acked.id, ReminderUpdate(status="acked"))
    snoozed = await repo.create(at(30, "snoozed"))
    await repo.update(snoozed.id, ReminderUpdate(status="snoozed"))

    assert [r.message for r in await repo.list_pending()] == ["soon", "snoozed", "later"]
    due = await repo.list_pending(before=T0 + timedelta(minutes=30))
    assert [r.id for r in due] == [soon.id, snoozed.id]
    assert later.id not in [r.id for r in due]


async def test_for_target(repo: ReminderRepo) -> None:
    first = await repo.create(at(0, target_kind="task", target_id=7))
    second = await repo.create(at(15, target_kind="task", target_id=7))
    await repo.create(at(5, target_kind="habit", target_id=7))
    await repo.update(second.id, ReminderUpdate(status="cancelled"))

    assert [r.id for r in await repo.for_target("task", 7)] == [first.id, second.id]
    assert [r.id for r in await repo.for_target("task", 7, ["scheduled"])] == [first.id]
    assert await repo.for_target("task", 8) == []


async def test_update_snooze_and_scoping(repo: ReminderRepo, session: AsyncSession) -> None:
    reminder = await repo.create(at(0))
    snoozed = await repo.update(
        reminder.id,
        ReminderUpdate(fire_at=T0 + timedelta(minutes=20), status="snoozed", job_id="reminder:1"),
    )
    assert snoozed is not None
    assert (snoozed.status, snoozed.fire_at, snoozed.job_id) == (
        "snoozed",
        T0 + timedelta(minutes=20),
        "reminder:1",
    )
    assert await repo.update(reminder.id, ReminderUpdate()) == snoozed

    other = ReminderRepo(session, "someone-else")
    assert await other.get(reminder.id) is None
    assert await other.list_pending() == []
    assert await other.update(reminder.id, ReminderUpdate(status="acked")) is None
