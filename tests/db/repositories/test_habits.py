from datetime import date, time

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.models import HabitLog
from sentry_ai.db.repositories import HabitRepo
from sentry_ai.db.schemas import HabitCreate, HabitUpdate

DSA = HabitCreate(name="DSA practice", rrule="FREQ=DAILY", preferred_time=time(21, 0))


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> HabitRepo:
    return HabitRepo(session, user_id)


async def test_create_get_and_lookup_by_name(repo: HabitRepo) -> None:
    habit = await repo.create(DSA)
    assert (habit.period, habit.target_per_period, habit.preferred_time) == ("day", 1, time(21))
    assert await repo.get(habit.id) == habit
    found = await repo.get_by_name("dsa PRACTICE ")
    assert found is not None and found.id == habit.id
    assert await repo.get_by_name("Reading") is None


async def test_list_update_archive(repo: HabitRepo) -> None:
    dsa = await repo.create(DSA)
    workout = await repo.create(
        HabitCreate(name="Workout", rrule="FREQ=DAILY", target_per_period=5, period="week")
    )
    assert [h.name for h in await repo.list_active()] == ["DSA practice", "Workout"]

    updated = await repo.update(dsa.id, HabitUpdate(preferred_time=time(22, 0)))
    assert updated is not None and updated.preferred_time == time(22)
    assert await repo.update(dsa.id, HabitUpdate()) == updated

    assert await repo.archive(workout.id)
    assert not await repo.archive(workout.id)
    assert [h.name for h in await repo.list_active()] == ["DSA practice"]
    assert await repo.get_by_name("Workout") is None


async def test_log_upserts_one_row_per_local_day(repo: HabitRepo) -> None:
    habit = await repo.create(DSA)
    day = date(2026, 11, 21)
    first = await repo.log(habit.id, day, "done", "2 mediums")
    again = await repo.log(habit.id, day, "skipped")
    assert first is not None and again is not None
    assert again.id == first.id
    assert (again.status, again.note) == ("skipped", None)
    assert [log.status for log in await repo.logs_between(habit.id, day, day)] == ["skipped"]


async def test_log_refuses_unknown_or_foreign_habits(
    repo: HabitRepo, session: AsyncSession
) -> None:
    habit = await repo.create(DSA)
    assert await repo.log(999_999, date(2026, 11, 21), "done") is None
    assert (
        await HabitRepo(session, "someone-else").log(habit.id, date(2026, 11, 21), "done") is None
    )
    assert await repo.logs_between(habit.id, date(2026, 11, 1), date(2026, 11, 30)) == []


async def test_logs_between_is_inclusive_and_ordered(repo: HabitRepo) -> None:
    habit = await repo.create(DSA)
    for day, status in ((22, "missed"), (20, "done"), (21, "skipped"), (23, "done")):
        await repo.log(habit.id, date(2026, 11, day), status)  # type: ignore[arg-type]
    logs = await repo.logs_between(habit.id, date(2026, 11, 20), date(2026, 11, 22))
    assert [(log.local_date.day, log.status) for log in logs] == [
        (20, "done"),
        (21, "skipped"),
        (22, "missed"),
    ]


async def test_delete_removes_habit_and_its_logs(repo: HabitRepo, session: AsyncSession) -> None:
    habit = await repo.create(DSA)
    await repo.log(habit.id, date(2026, 11, 21), "done")
    assert not await HabitRepo(session, "someone-else").delete(habit.id)
    assert await repo.delete(habit.id)
    assert await repo.get(habit.id) is None
    remaining = await session.scalar(
        sa.select(sa.func.count()).select_from(HabitLog).where(HabitLog.habit_id == habit.id)
    )
    assert remaining == 0
    assert not await repo.delete(habit.id)
