from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.config import get_settings
from sentry_ai.db.repositories import HabitRepo, RoutineRepo
from sentry_ai.db.schemas import HabitCreate, HabitDTO, RoutineCreate, RoutineDTO
from sentry_ai.services import time as t
from sentry_ai.services.routines import expand, expand_routines, habits_due

CREATED = datetime(2026, 11, 1, 6, 0, tzinfo=UTC)
MON, SAT, SUN = date(2026, 11, 23), date(2026, 11, 28), date(2026, 11, 29)
WEEKDAYS = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
MON_TO_SAT = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"


@pytest.fixture(autouse=True)
def ist(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("USER_TZ", "Asia/Kolkata")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def routine(id_: int, title: str, rrule: str, start: time, minutes: int, **kw: Any) -> RoutineDTO:
    fields = {
        "id": id_,
        "title": title,
        "category": "study",
        "rrule": rrule,
        "start_time": start,
        "duration_min": minutes,
        "is_fixed": True,
        "remind_before_min": None,
        "active_from": None,
        "active_until": None,
        "archived_at": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return RoutineDTO.model_validate(fields | kw)


def habit(id_: int, name: str, rrule: str, at: time | None, **kw: Any) -> HabitDTO:
    fields = {
        "id": id_,
        "name": name,
        "rrule": rrule,
        "target_per_period": 1,
        "period": "day",
        "preferred_time": at,
        "remind_before_min": None,
        "archived_at": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return HabitDTO.model_validate(fields | kw)


PROFILE_ROUTINES = [
    routine(1, "College classes", WEEKDAYS, time(9, 0), 300, remind_before_min=30),
    routine(2, "Gym", MON_TO_SAT, time(18, 0), 90, category="fitness"),
    routine(3, "Lunch", "FREQ=DAILY", time(13, 30), 45, category="health", is_fixed=False),
]


def local_hhmm(at: datetime) -> str:
    return t.to_local(at).strftime("%H:%M")


def test_weekday_has_classes_lunch_and_gym_in_order() -> None:
    blocks = expand_routines(PROFILE_ROUTINES, MON)
    assert [(b.title, local_hhmm(b.start_at), local_hhmm(b.end_at)) for b in blocks] == [
        ("College classes", "09:00", "14:00"),
        ("Lunch", "13:30", "14:15"),
        ("Gym", "18:00", "19:30"),
    ]
    classes = blocks[0]
    assert classes.start_at == datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    assert (classes.is_fixed, classes.remind_before_min) == (True, 30)
    assert blocks[1].is_fixed is False


def test_saturday_drops_classes_and_sunday_only_has_lunch() -> None:
    assert [b.title for b in expand_routines(PROFILE_ROUTINES, SAT)] == ["Lunch", "Gym"]
    assert [b.title for b in expand_routines(PROFILE_ROUTINES, SUN)] == ["Lunch"]


def test_active_window_and_archive_are_respected() -> None:
    exam = routine(
        4,
        "Exam revision",
        "FREQ=DAILY",
        time(7, 0),
        60,
        active_from=date(2026, 11, 24),
        active_until=date(2026, 11, 28),
    )
    archived = routine(5, "Old club", "FREQ=DAILY", time(8, 0), 60, archived_at=CREATED)
    titles = {d: [b.title for b in expand_routines([exam, archived], d)] for d in (MON, SAT, SUN)}
    assert titles == {MON: [], SAT: ["Exam revision"], SUN: []}


def test_block_running_past_midnight_belongs_to_its_start_day() -> None:
    late = routine(6, "Night shift", "FREQ=DAILY", time(23, 30), 90)
    [block] = expand_routines([late], MON)
    assert t.local_date_of(block.start_at) == MON
    assert t.local_date_of(block.end_at) == MON + timedelta(days=1)
    assert local_hhmm(block.end_at) == "01:00"
    [next_block] = expand_routines([late], MON + timedelta(days=1))  # its own instance
    assert next_block.start_at == block.start_at + timedelta(days=1)


def test_nothing_before_the_rows_start() -> None:
    fresh = routine(
        7, "New", "FREQ=DAILY", time(9, 0), 30, created_at=datetime(2026, 11, 25, tzinfo=UTC)
    )
    assert expand_routines([fresh], MON) == []
    assert len(expand_routines([fresh], SAT)) == 1


def test_habits_due_timed_first_then_untimed() -> None:
    habits = [
        habit(1, "DSA practice", "FREQ=DAILY", time(21, 0)),
        habit(2, "Workout", MON_TO_SAT, None, period="week", target_per_period=5),
        habit(3, "Reading (20 min)", "FREQ=DAILY", time(23, 0)),
        habit(4, "Retired", "FREQ=DAILY", time(7, 0), archived_at=CREATED),
    ]
    monday = habits_due(habits, MON)
    assert [h.name for h in monday] == ["DSA practice", "Reading (20 min)", "Workout"]
    assert monday[0].preferred_at == datetime(2026, 11, 23, 15, 30, tzinfo=UTC)  # 21:00 IST
    assert monday[2].preferred_at is None and monday[2].period == "week"
    assert [h.name for h in habits_due(habits, SUN)] == ["DSA practice", "Reading (20 min)"]


async def test_expand_loads_from_the_database_for_a_weekday_and_weekend(
    session: AsyncSession, user_id: str
) -> None:
    routines, habits = RoutineRepo(session, user_id), HabitRepo(session, user_id)
    for r in PROFILE_ROUTINES:
        await routines.create(
            RoutineCreate.model_validate(
                r.model_dump(include=set(RoutineCreate.model_fields))
                | {"active_from": date(2026, 1, 1)}
            )
        )
    await habits.create(
        HabitCreate(name="DSA practice", rrule="FREQ=DAILY", preferred_time=time(21))
    )
    # habits anchor on their creation day (today), so test days are relative to today
    today = t.today_local()
    next_monday = today + timedelta(days=7 - today.weekday())
    next_saturday = next_monday + timedelta(days=5)

    weekday = await expand(next_monday, session=session, user_id=user_id)
    assert weekday.local_date == next_monday
    assert [b.title for b in weekday.routines] == ["College classes", "Lunch", "Gym"]
    assert [h.name for h in weekday.habits] == ["DSA practice"]

    weekend = await expand(next_saturday, session=session, user_id=user_id)
    assert [b.title for b in weekend.routines] == ["Lunch", "Gym"]
    assert await expand(next_monday, session=session, user_id="someone-else") == weekday.model_copy(
        update={"routines": [], "habits": []}
    )
