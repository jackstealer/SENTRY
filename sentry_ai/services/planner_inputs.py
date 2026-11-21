"""Load one local day's planner inputs from the DB: the only I/O around the pure planner."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import (
    CalendarRepo,
    HabitRepo,
    PreferenceRepo,
    RoutineRepo,
    TaskRepo,
)
from sentry_ai.db.repositories.tasks import OPEN_STATUSES
from sentry_ai.services.intervals import Interval
from sentry_ai.services.planner import (
    DEFAULT_TASK_MIN,
    HABIT_BLOCK_MIN,
    Busy,
    Flexible,
    Prefs,
    TaskItem,
)
from sentry_ai.services.routines import expand_routines, habits_due
from sentry_ai.services.time import day_bounds_utc

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class DayInputs:
    prefs: Prefs
    busy: list[Busy]  # everything fixed that can touch the day's frame, spill-overs included
    flexible: list[Flexible]
    tasks: list[TaskItem]


async def gather(day: date, *, session: AsyncSession, user_id: str) -> DayInputs:
    """Load what plan_day needs for one local day."""
    prefs = Prefs.from_preferences(await PreferenceRepo(session, user_id).all())
    # a frame never leaves [day 00:00, day+2 00:00) local, so nothing outside it matters
    reach = Interval(day_bounds_utc(day)[0], day_bounds_utc(day + 2 * _ONE_DAY)[0])
    routines = await RoutineRepo(session, user_id).list_active()
    busy: list[Busy] = []
    flexible: list[Flexible] = []
    for d in (day - _ONE_DAY, day, day + _ONE_DAY):
        for block in expand_routines(routines, d):
            span = Interval(block.start_at, block.end_at)
            if block.is_fixed and span.overlaps(reach):
                busy.append(Busy(span.start, span.end, block.title, "routine", block.routine_id))
            elif not block.is_fixed and d == day:
                flexible.append(
                    Flexible("routine", block.routine_id, block.title, span.start, span.minutes)
                )
    for event in await CalendarRepo(session, user_id).between(reach.start, reach.end):
        # SENTRY's own events mirror plan blocks (Phase 6); all-day events do not block time
        if not event.all_day and not event.is_sentry_owned:
            busy.append(Busy(event.start_at, event.end_at, event.title, "calendar_event", event.id))
    habits = HabitRepo(session, user_id)
    for habit in habits_due(await habits.list_active(), day):
        if habit.preferred_at is None or await habits.logs_between(habit.habit_id, day, day):
            continue  # untimed habits are not placed; logged ones are already handled
        flexible.append(
            Flexible("habit", habit.habit_id, habit.name, habit.preferred_at, HABIT_BLOCK_MIN)
        )
    # shortcut: the 50 most urgent open tasks; enough for one person's day
    open_tasks = await TaskRepo(session, user_id).query(status=OPEN_STATUSES, limit=50)
    tasks = [
        TaskItem(t.id, t.title, t.priority, t.due_at, t.estimate_min or DEFAULT_TASK_MIN, t.project)
        for t in open_tasks
    ]
    return DayInputs(prefs, busy, flexible, tasks)
