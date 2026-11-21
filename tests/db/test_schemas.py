from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from sentry_ai.db.models import EpisodicEntry, Task
from sentry_ai.db.schemas import (
    EpisodeDTO,
    HabitCreate,
    MemoryCreate,
    PlanBlockCreate,
    PlanBlockUpdate,
    ReminderCreate,
    RoutineCreate,
    RoutineUpdate,
    TaskCreate,
    TaskDTO,
    TaskUpdate,
    validate_rrule,
)

IST = ZoneInfo("Asia/Kolkata")
FRI_5PM_IST = datetime(2026, 11, 27, 17, 0, tzinfo=IST)
NAIVE = datetime(2026, 11, 27, 17, 0)


def routine(**overrides: object) -> RoutineCreate:
    fields: dict[str, object] = {
        "title": "Gym",
        "category": "fitness",
        "rrule": "FREQ=DAILY",
        "start_time": time(18, 0),
        "duration_min": 90,
    } | overrides
    return RoutineCreate.model_validate(fields)


@pytest.mark.parametrize(
    "build",
    [
        lambda dt: TaskCreate(title="x", due_at=dt),
        lambda dt: ReminderCreate(message="x", fire_at=dt),
        lambda dt: PlanBlockCreate(
            start_at=dt, end_at=dt + timedelta(hours=1), kind="task", title="x"
        ),
        lambda dt: TaskUpdate(scheduled_start=dt),
    ],
)
def test_naive_datetimes_are_rejected_everywhere(build: object) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        build(NAIVE)  # type: ignore[operator]


def test_aware_ist_datetime_keeps_its_offset() -> None:
    task = TaskCreate(title="Submit SE assignment", due_at=FRI_5PM_IST)
    assert task.due_at == FRI_5PM_IST
    assert task.due_at is not None and task.due_at.astimezone(UTC).hour == 11  # 17:00 IST


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("freq=weekly;byday=mo,we", "FREQ=WEEKLY;BYDAY=MO,WE"),
        ("RRULE:FREQ=DAILY", "FREQ=DAILY"),
        ("  FREQ=DAILY;INTERVAL=2  ", "FREQ=DAILY;INTERVAL=2"),
    ],
)
def test_rrule_is_normalized(raw: str, normalized: str) -> None:
    assert validate_rrule(raw) == normalized


@pytest.mark.parametrize(
    "bad",
    ["", "FREQ=SOMETIMES", "DTSTART:20260101T000000Z\nRRULE:FREQ=DAILY", "FREQ=DAILY;DTSTART=1"],
)
def test_bad_rrules_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="rrule"):
        validate_rrule(bad)
    with pytest.raises(ValidationError):
        routine(rrule=bad)


def test_routine_field_rules() -> None:
    assert routine().is_fixed is True
    for overrides in (
        {"duration_min": 0},
        {"category": "napping"},
        {"title": "   "},
        {"active_from": date(2026, 12, 1), "active_until": date(2026, 11, 1)},
        {"unexpected": 1},
    ):
        with pytest.raises(ValidationError):
            routine(**overrides)


def test_update_writes_only_fields_that_were_set() -> None:
    assert TaskUpdate(priority=1).changes() == {"priority": 1}
    assert TaskUpdate(estimate_min=None).changes() == {"estimate_min": None}  # nullable: allowed
    assert RoutineUpdate(rrule="freq=daily").changes() == {"rrule": "FREQ=DAILY"}


@pytest.mark.parametrize("field", ["title", "priority"])
def test_update_refuses_null_for_not_null_columns(field: str) -> None:
    with pytest.raises(ValidationError, match="cannot set"):
        TaskUpdate.model_validate({field: None})


def test_plan_blocks_must_end_after_they_start() -> None:
    with pytest.raises(ValidationError, match="after start_at"):
        PlanBlockCreate(start_at=FRI_5PM_IST, end_at=FRI_5PM_IST, kind="task", title="x")
    with pytest.raises(ValidationError, match="after start_at"):
        PlanBlockUpdate(start_at=FRI_5PM_IST, end_at=FRI_5PM_IST - timedelta(minutes=1))


def test_reminder_target_rules() -> None:
    assert ReminderCreate(message="x", fire_at=FRI_5PM_IST).target_kind == "freeform"
    assert ReminderCreate(message="x", fire_at=FRI_5PM_IST, target_kind="task", target_id=4)
    with pytest.raises(ValidationError, match="target_id"):
        ReminderCreate(message="x", fire_at=FRI_5PM_IST, target_kind="task")
    with pytest.raises(ValidationError, match="target_id"):
        ReminderCreate(message="x", fire_at=FRI_5PM_IST, target_id=4)


def test_embedding_must_match_schema_dimensions() -> None:
    MemoryCreate(content="x", kind="fact", embedding=[0.0] * 1536, source="explicit")
    with pytest.raises(ValidationError):
        MemoryCreate(content="x", kind="fact", embedding=[0.0] * 3072, source="explicit")


def test_weekly_habit_target_fits_in_a_week() -> None:
    assert HabitCreate(name="Workout", rrule="FREQ=DAILY", target_per_period=5, period="week")
    with pytest.raises(ValidationError, match="7 times"):
        HabitCreate(name="Workout", rrule="FREQ=DAILY", target_per_period=8, period="week")


def test_dtos_read_orm_rows_and_are_frozen() -> None:
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    row = Task(
        id=1,
        title="DSA set",
        notes=None,
        status="todo",
        priority=2,
        due_at=None,
        estimate_min=60,
        scheduled_start=None,
        project="DSA",
        source="chat",
        completed_at=None,
        rolled_over_count=0,
        archived_at=None,
        created_at=now,
        updated_at=now,
    )
    dto = TaskDTO.model_validate(row)
    assert (dto.id, dto.title, dto.estimate_min) == (1, "DSA set", 60)
    with pytest.raises(ValidationError):
        dto.title = "changed"  # type: ignore[misc]


def test_dto_rejects_a_naive_timestamp_from_the_db() -> None:
    row = EpisodicEntry(
        id=1, trace_id="t", role="user", content="hi", meta={"intent": "capture"}, created_at=NAIVE
    )
    with pytest.raises(ValidationError, match="timezone"):
        EpisodeDTO.model_validate(row)


def test_episode_dto_exposes_meta_column_as_metadata() -> None:
    now = datetime(2026, 11, 21, tzinfo=UTC)
    row = EpisodicEntry(
        id=1, trace_id="t", role="user", content="hi", meta={"k": 1}, created_at=now
    )
    assert EpisodeDTO.model_validate(row).metadata == {"k": 1}
