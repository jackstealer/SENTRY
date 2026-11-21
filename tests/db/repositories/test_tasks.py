from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import TaskRepo
from sentry_ai.db.schemas import TaskCreate, TaskUpdate

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> TaskRepo:
    return TaskRepo(session, user_id)


async def test_create_applies_defaults_and_stores_utc(repo: TaskRepo) -> None:
    due = datetime(2026, 11, 27, 17, 0, tzinfo=IST)
    task = await repo.create(TaskCreate(title="Submit SE assignment", due_at=due))
    assert (task.status, task.priority, task.source, task.rolled_over_count) == (
        "todo",
        2,
        "chat",
        0,
    )
    assert task.due_at == due  # same instant...
    assert task.due_at is not None and task.due_at.utcoffset() == timedelta(0)  # ...read as UTC
    assert await repo.get(task.id) == task


async def test_query_filters_and_orders_by_urgency(repo: TaskRepo) -> None:
    soon = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    await repo.create(TaskCreate(title="p2 undated"))
    await repo.create(TaskCreate(title="p2 later", due_at=soon + timedelta(days=2)))
    await repo.create(TaskCreate(title="p2 soon", due_at=soon))
    await repo.create(TaskCreate(title="p1 urgent", priority=1, project="DSA"))
    done = await repo.create(TaskCreate(title="p1 done", priority=1))
    await repo.set_status(done.id, "done", soon)

    titles = [t.title for t in await repo.query(status=["todo"])]
    assert titles == ["p1 urgent", "p2 soon", "p2 later", "p2 undated"]
    assert [t.title for t in await repo.query(project="dsa")] == ["p1 urgent"]
    assert len(await repo.query(limit=2)) == 2
    assert {t.title for t in await repo.query(status=["done"])} == {"p1 done"}


async def test_due_before_respects_the_ist_day_boundary(repo: TaskRepo) -> None:
    # local day 21 Nov (IST) ends at 21 Nov 18:30 UTC
    end_of_day = datetime(2026, 11, 22, 0, 0, tzinfo=IST)
    await repo.create(
        TaskCreate(title="23:50 IST", due_at=datetime(2026, 11, 21, 23, 50, tzinfo=IST))
    )
    await repo.create(
        TaskCreate(title="00:30 IST", due_at=datetime(2026, 11, 22, 0, 30, tzinfo=IST))
    )
    assert [t.title for t in await repo.query(due_before=end_of_day)] == ["23:50 IST"]


async def test_update_and_scoping(repo: TaskRepo, session: AsyncSession) -> None:
    task = await repo.create(TaskCreate(title="Read chapter 4", estimate_min=30))
    updated = await repo.update(task.id, TaskUpdate(priority=1, estimate_min=None))
    assert updated is not None and (updated.priority, updated.estimate_min) == (1, None)
    assert await repo.update(task.id, TaskUpdate()) == updated

    other = TaskRepo(session, "someone-else")
    assert await other.get(task.id) is None
    assert await other.update(task.id, TaskUpdate(title="x")) is None
    assert await other.set_status(task.id, "done", datetime.now(UTC)) is None
    assert await other.query() == []


async def test_set_status_sets_and_clears_completed_at(repo: TaskRepo) -> None:
    task = await repo.create(TaskCreate(title="Gym bag"))
    at = datetime(2026, 11, 21, 18, 20, tzinfo=UTC)
    done = await repo.set_status(task.id, "done", at)
    assert done is not None and (done.status, done.completed_at) == ("done", at)
    reopened = await repo.set_status(task.id, "todo", at)
    assert reopened is not None and (reopened.status, reopened.completed_at) == ("todo", None)


async def test_increment_rollover(repo: TaskRepo) -> None:
    task = await repo.create(TaskCreate(title="DSA set"))
    await repo.increment_rollover(task.id)
    bumped = await repo.increment_rollover(task.id)
    assert bumped is not None and bumped.rolled_over_count == 2
    assert await repo.increment_rollover(999_999) is None


async def test_find_similar_matches_fuzzy_titles(repo: TaskRepo) -> None:
    se = await repo.create(TaskCreate(title="Submit SE assignment"))
    dsa = await repo.create(TaskCreate(title="DSA graph problems"))
    await repo.create(TaskCreate(title="Buy groceries"))
    closed = await repo.create(TaskCreate(title="SE assignment draft"))
    await repo.set_status(closed.id, "done", datetime.now(UTC))

    matches = await repo.find_similar("SE assignment")
    assert [m.task.id for m in matches] == [se.id]  # done task excluded by default
    assert 0.3 <= matches[0].similarity <= 1.0
    assert (await repo.find_similar("graph"))[0].task.id == dsa.id
    assert await repo.find_similar("zzzz qqqq") == []
    with_done = await repo.find_similar("SE assignment", statuses=["todo", "done"])
    assert {m.task.id for m in with_done} == {se.id, closed.id}


async def test_archive_done_before(repo: TaskRepo) -> None:
    old = await repo.create(TaskCreate(title="old done"))
    recent = await repo.create(TaskCreate(title="recent done"))
    await repo.create(TaskCreate(title="still todo"))
    await repo.set_status(old.id, "done", datetime(2026, 10, 1, tzinfo=UTC))
    await repo.set_status(recent.id, "done", datetime(2026, 11, 20, tzinfo=UTC))

    assert await repo.archive_done_before(datetime(2026, 11, 1, tzinfo=UTC)) == 1
    assert await repo.archive_done_before(datetime(2026, 11, 1, tzinfo=UTC)) == 0
    assert {t.title for t in await repo.query()} == {"recent done", "still todo"}
