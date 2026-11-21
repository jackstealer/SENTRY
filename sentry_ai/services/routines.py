"""Expand recurring routines and habits into one local day's concrete instances.

Nothing is stored: instances are computed on demand, so editing a routine changes every
future day at once. An RRULE is anchored on `active_from`, or on the day the row was created.
"""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.enums import HabitPeriod, RoutineCategory
from sentry_ai.db.repositories import HabitRepo, RoutineRepo
from sentry_ai.db.schemas import HabitDTO, RoutineDTO
from sentry_ai.services.time import local_date_of, occurrences_on, to_utc

_UNTIMED_LAST = datetime.max.replace(tzinfo=UTC)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class RoutineBlock(_Frozen):
    routine_id: int
    title: str
    category: RoutineCategory
    start_at: AwareDatetime  # UTC
    end_at: AwareDatetime  # UTC; may fall on the next local day
    is_fixed: bool
    remind_before_min: int | None


class HabitDue(_Frozen):
    habit_id: int
    name: str
    period: HabitPeriod
    target_per_period: int
    preferred_at: AwareDatetime | None  # UTC; None when the habit has no preferred time
    remind_before_min: int | None


class DayInstances(_Frozen):
    local_date: date
    routines: list[RoutineBlock]
    habits: list[HabitDue]


def _anchor(active_from: date | None, created_at: datetime) -> date:
    return active_from or local_date_of(created_at)


def _active_on(routine: RoutineDTO, on: date) -> bool:
    if routine.archived_at is not None:
        return False
    if routine.active_from is not None and on < routine.active_from:
        return False
    return routine.active_until is None or on <= routine.active_until


def expand_routines(routines: Sequence[RoutineDTO], on: date) -> list[RoutineBlock]:
    """Routine blocks that start on local day `on`, earliest first."""
    blocks: list[RoutineBlock] = []
    for routine in routines:
        if not _active_on(routine, on):
            continue
        anchor = _anchor(routine.active_from, routine.created_at)
        for start in occurrences_on(routine.rrule, anchor, routine.start_time, on):
            start_utc = to_utc(start)  # add durations in UTC: correct across DST changes too
            blocks.append(
                RoutineBlock(
                    routine_id=routine.id,
                    title=routine.title,
                    category=routine.category,
                    start_at=start_utc,
                    end_at=start_utc + timedelta(minutes=routine.duration_min),
                    is_fixed=routine.is_fixed,
                    remind_before_min=routine.remind_before_min,
                )
            )
    return sorted(blocks, key=lambda block: (block.start_at, block.routine_id))


def habits_due(habits: Sequence[HabitDTO], on: date) -> list[HabitDue]:
    """Habits whose RRULE includes local day `on`; timed ones first, by preferred time."""
    due: list[HabitDue] = []
    for habit in habits:
        if habit.archived_at is not None:
            continue
        anchor = local_date_of(habit.created_at)
        hits = occurrences_on(habit.rrule, anchor, habit.preferred_time or time.min, on)
        if not hits:
            continue
        due.append(
            HabitDue(
                habit_id=habit.id,
                name=habit.name,
                period=habit.period,
                target_per_period=habit.target_per_period,
                preferred_at=to_utc(hits[0]) if habit.preferred_time else None,
                remind_before_min=habit.remind_before_min,
            )
        )
    return sorted(due, key=lambda h: (h.preferred_at or _UNTIMED_LAST, h.name))


async def expand(on: date, *, session: AsyncSession, user_id: str) -> DayInstances:
    """Everything recurring that lands on local day `on`, loaded from the database."""
    routines = await RoutineRepo(session, user_id).list_active(on=on)
    habits = await HabitRepo(session, user_id).list_active()
    return DayInstances(
        local_date=on, routines=expand_routines(routines, on), habits=habits_due(habits, on)
    )
