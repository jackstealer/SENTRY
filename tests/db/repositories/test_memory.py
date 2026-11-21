from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.models import EMBEDDING_DIMS
from sentry_ai.db.repositories import MemoryRepo
from sentry_ai.db.schemas import MemoryCreate


def axis(i: int, wobble: float = 0.0) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMS
    vector[i] = 1.0
    vector[(i + 1) % EMBEDDING_DIMS] = wobble
    return vector


def memory(content: str, vector: list[float], kind: str = "preference") -> MemoryCreate:
    return MemoryCreate.model_validate(
        {"content": content, "kind": kind, "embedding": vector, "source": "explicit"}
    )


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> MemoryRepo:
    return MemoryRepo(session, user_id)


async def test_log_episode_and_read_a_window(repo: MemoryRepo, session: AsyncSession) -> None:
    entry = await repo.log_episode("trace-1", "user", "plan my day", {"intent": "plan"})
    assert (entry.role, entry.metadata) == ("user", {"intent": "plan"})
    bare = await repo.log_episode("trace-1", "assistant", "Here is your plan")
    assert bare.metadata == {}

    now = await session.scalar(sa.select(sa.func.now()))
    assert now is not None
    hour = timedelta(hours=1)
    assert [e.id for e in await repo.episodes_between(now - hour, now + hour)] == [
        entry.id,
        bare.id,
    ]
    assert await repo.episodes_between(now + hour, now + 2 * hour) == []


async def test_add_get_and_scoping(repo: MemoryRepo, session: AsyncSession) -> None:
    created = await repo.add(memory("Prefers deep work after 9pm", axis(0)))
    assert (created.kind, created.source, created.confidence) == ("preference", "explicit", 1.0)
    assert await repo.get(created.id) == created
    other = MemoryRepo(session, "someone-else")
    assert await other.get(created.id) is None
    assert await other.search(axis(0)) == []


async def test_search_ranks_by_cosine_distance(repo: MemoryRepo) -> None:
    night = await repo.add(memory("Focuses best at night", axis(0)))
    gym = await repo.add(memory("Goes to the gym at 6pm", axis(1)))
    goal = await repo.add(memory("Wants a placement by March", axis(0, wobble=0.5), kind="goal"))

    matches = await repo.search(axis(0, wobble=0.1), k=2)
    assert [m.memory.id for m in matches] == [night.id, goal.id]
    assert matches[0].distance < matches[1].distance
    assert [m.memory.id for m in await repo.search(axis(1), k=1)] == [gym.id]
    assert [m.memory.id for m in await repo.search(axis(0), kinds=["goal"])] == [goal.id]


async def test_search_skips_superseded_and_checks_dims(repo: MemoryRepo) -> None:
    old = await repo.add(memory("Gym at 6pm", axis(0)))
    new = await repo.add(memory("Gym at 7am now", axis(0, wobble=0.01)))
    assert await repo.supersede(old.id, new.id)
    assert [m.memory.id for m in await repo.search(axis(0))] == [new.id]
    assert not await repo.supersede(999_999, new.id)
    with pytest.raises(ValueError, match="dims"):
        await repo.search([1.0, 0.0])


async def test_touch_marks_last_used(repo: MemoryRepo) -> None:
    created = await repo.add(memory("Prefers mornings off", axis(2)))
    used_at = datetime(2026, 11, 21, 18, 20, tzinfo=UTC)
    await repo.touch([created.id], used_at)
    await repo.touch([], used_at)  # no-op
    touched = await repo.get(created.id)
    assert touched is not None and touched.last_used_at == used_at


async def test_delete_unlinks_memories_it_superseded(repo: MemoryRepo) -> None:
    old = await repo.add(memory("Gym at 6pm", axis(0)))
    new = await repo.add(memory("Gym at 7am", axis(0)))
    await repo.supersede(old.id, new.id)
    assert await repo.delete(new.id)
    revived = await repo.get(old.id)
    assert revived is not None and revived.superseded_by is None
    assert not await repo.delete(new.id)
