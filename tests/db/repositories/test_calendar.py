from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import CalendarRepo
from sentry_ai.db.schemas import CalendarEventUpsert

T0 = datetime(2026, 11, 22, 4, 0, tzinfo=UTC)  # 09:30 IST


def event(
    event_id: str, start_h: float, hours: float, title: str = "Lecture"
) -> CalendarEventUpsert:
    start = T0 + timedelta(hours=start_h)
    return CalendarEventUpsert(
        gcal_event_id=event_id,
        calendar_id="primary",
        title=title,
        start_at=start,
        end_at=start + timedelta(hours=hours),
        synced_at=T0,
    )


@pytest.fixture
def repo(session: AsyncSession, user_id: str) -> CalendarRepo:
    return CalendarRepo(session, user_id)


async def test_upsert_inserts_then_refreshes(repo: CalendarRepo) -> None:
    assert await repo.upsert_events([event("a", 0, 1), event("b", 2, 1)]) == 2
    assert await repo.upsert_events([event("a", 0, 1.5, title="Lecture (moved)")]) == 1
    events = await repo.between(T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert [(e.gcal_event_id, e.title) for e in events] == [
        ("a", "Lecture (moved)"),
        ("b", "Lecture"),
    ]
    assert events[0].end_at == T0 + timedelta(hours=1.5)
    assert await repo.upsert_events([]) == 0


async def test_between_returns_overlaps_only(repo: CalendarRepo) -> None:
    await repo.upsert_events(
        [
            event("before", -2, 1),  # ends 1 h before the window
            event("touching", -1, 1),  # ends exactly at the window start: excluded
            event("spanning", -1, 4),
            event("inside", 0.5, 0.5),
            event("after", 3, 1),  # starts exactly at the window end: excluded
        ]
    )
    window = await repo.between(T0, T0 + timedelta(hours=3))
    assert [e.gcal_event_id for e in window] == ["spanning", "inside"]


async def test_delete_and_scoping(repo: CalendarRepo, session: AsyncSession) -> None:
    await repo.upsert_events([event("a", 0, 1)])
    other = CalendarRepo(session, "someone-else")
    assert await other.between(T0 - timedelta(days=1), T0 + timedelta(days=1)) == []
    assert not await other.delete("primary", "a")
    assert await repo.delete("primary", "a")
    assert not await repo.delete("primary", "a")
