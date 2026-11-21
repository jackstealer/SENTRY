from datetime import date, time
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import RoutineRepo
from sentry_ai.db.schemas import RoutineCreate, RoutineUpdate


def gym(**overrides: Any) -> RoutineCreate:
    fields = {
        "title": "Gym",
        "category": "fitness",
        "rrule": "FREQ=WEEKLY;BYDAY=MO,WE,FR",
        "start_time": time(18, 0),
        "duration_min": 90,
    }
    return RoutineCreate.model_validate(fields | overrides)


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> RoutineRepo:
    return RoutineRepo(session, user_id)


async def test_create_and_get(repo: RoutineRepo) -> None:
    created = await repo.create(gym(remind_before_min=20))
    assert created.id > 0
    assert (created.title, created.is_fixed, created.remind_before_min) == ("Gym", True, 20)
    assert created.created_at.tzinfo is not None
    assert await repo.get(created.id) == created
    assert await repo.get(999_999) is None


async def test_everything_is_scoped_to_the_user(repo: RoutineRepo, session: AsyncSession) -> None:
    created = await repo.create(gym())
    other = RoutineRepo(session, "someone-else")
    assert await other.get(created.id) is None
    assert await other.get_by_title("Gym") is None
    assert await other.list_active() == []
    assert await other.update(created.id, RoutineUpdate(title="Hijacked")) is None
    assert not await other.archive(created.id)
    assert not await other.delete(created.id)
    assert await repo.get(created.id) == created


async def test_get_by_title_ignores_case_and_archived(repo: RoutineRepo) -> None:
    created = await repo.create(gym())
    found = await repo.get_by_title("  gYM ")
    assert found is not None and found.id == created.id
    await repo.archive(created.id)
    assert await repo.get_by_title("gym") is None


async def test_list_active_orders_by_start_and_respects_window(repo: RoutineRepo) -> None:
    await repo.create(gym(title="Late", start_time=time(21, 0)))
    await repo.create(gym(title="Early", start_time=time(7, 0)))
    await repo.create(
        gym(title="Exam prep", active_from=date(2026, 12, 1), active_until=date(2026, 12, 10))
    )

    def titles(rows: list[Any]) -> list[str]:
        return [r.title for r in rows]

    assert titles(await repo.list_active()) == ["Early", "Exam prep", "Late"]
    assert titles(await repo.list_active(on=date(2026, 11, 30))) == ["Early", "Late"]
    for inside in (date(2026, 12, 1), date(2026, 12, 10)):  # window is inclusive
        assert "Exam prep" in titles(await repo.list_active(on=inside))
    assert "Exam prep" not in titles(await repo.list_active(on=date(2026, 12, 11)))


async def test_update_is_partial(repo: RoutineRepo) -> None:
    created = await repo.create(gym(remind_before_min=20))
    updated = await repo.update(
        created.id, RoutineUpdate(start_time=time(19, 0), remind_before_min=None)
    )
    assert updated is not None
    assert (updated.start_time, updated.remind_before_min, updated.title) == (time(19), None, "Gym")
    assert await repo.update(created.id, RoutineUpdate()) == updated  # no-op returns current
    assert await repo.update(999_999, RoutineUpdate(title="x")) is None


async def test_archive_is_soft_and_happens_once(repo: RoutineRepo) -> None:
    created = await repo.create(gym())
    assert await repo.archive(created.id)
    assert not await repo.archive(created.id)
    archived = await repo.get(created.id)
    assert archived is not None and archived.archived_at is not None
    assert await repo.list_active() == []


async def test_delete_is_hard(repo: RoutineRepo) -> None:
    created = await repo.create(gym())
    assert await repo.delete(created.id)
    assert await repo.get(created.id) is None
    assert not await repo.delete(created.id)
