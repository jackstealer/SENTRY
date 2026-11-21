"""Habit tools (TOOLS_SPEC section 4, Habits). Status/streak reads live in query.py."""

import datetime as dt
from typing import Any, Literal

from pydantic import Field, model_validator

from sentry_ai.db.repositories import HabitRepo
from sentry_ai.db.schemas import HabitCreate, HabitDTO, HabitUpdate
from sentry_ai.services.streaks import compute_streak
from sentry_ai.tools.base import (
    LocalTime,
    ToolArgs,
    ToolContext,
    ToolResult,
    ToolTier,
    fail,
    ok,
    tool,
)

RRULE = 'RFC 5545 RRULE body, local time, e.g. "FREQ=DAILY" or "FREQ=WEEKLY;BYDAY=MO,WE,FR"'
HHMM = 'local time "HH:MM", 24-hour'


def view(habit: HabitDTO) -> dict[str, Any]:
    return habit.model_dump(exclude={"archived_at", "created_at", "updated_at"})


class CreateHabitArgs(HabitCreate):
    rrule: str = Field(description=RRULE + " (the days it is due)")
    preferred_time: LocalTime | None = Field(default=None, description=HHMM)
    period: Literal["day", "week"] = Field(
        default="day", description='"week" with target_per_period=4 means 4 times a week'
    )


@tool(ToolTier.WRITE, CreateHabitArgs)
async def create_habit(args: CreateHabitArgs, ctx: ToolContext) -> ToolResult:
    """Start tracking a habit ("DSA practice every day at 9pm", "gym 4 times a week").

    Not for fixed calendar blocks like classes: use create_routine. Not for one-off to-dos.
    """
    habits = HabitRepo(ctx.session, ctx.user_id)
    if await habits.get_by_name(args.name) is not None:
        return fail("duplicate", f"A habit named {args.name!r} already exists; use update_habit.")
    habit = await habits.create(HabitCreate.model_validate(args.model_dump()))
    return ok(f"Now tracking habit {habit.id} '{habit.name}'.", habit=view(habit))


class LogHabitArgs(ToolArgs):
    habit_id: int | None = None
    habit_name: str | None = Field(default=None, description="exact name, any case")
    status: Literal["done", "skipped"]
    date: dt.date | None = Field(default=None, description="local day; defaults to today")
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_way_to_name_it(self) -> "LogHabitArgs":
        if (self.habit_id is None) == (self.habit_name is None):
            raise ValueError("give exactly one of habit_id or habit_name")
        return self


@tool(ToolTier.WRITE, LogHabitArgs)
async def log_habit(args: LogHabitArgs, ctx: ToolContext) -> ToolResult:
    """Record a habit as done or skipped for a day (today unless `date` is given).

    Logging the same habit and day again replaces that day's entry. Returns the new streak.
    """
    habits = HabitRepo(ctx.session, ctx.user_id)
    if args.habit_name is not None:
        habit = await habits.get_by_name(args.habit_name)
        if habit is None:
            names = ", ".join(repr(h.name) for h in await habits.list_active()) or "none yet"
            return fail("not_found", f"No habit named {args.habit_name!r}. Habits: {names}.")
    else:
        habit = await habits.get(args.habit_id or 0)
        if habit is None or habit.archived_at is not None:
            return fail("not_found", f"There is no habit {args.habit_id}.")
    day = args.date or ctx.today
    if day > ctx.today:
        return fail(
            "invalid_args", f"{day} is in the future; habits are logged for today or before."
        )
    logged = await habits.log(habit.id, day, args.status, args.note)
    if logged is None:
        return fail("not_found", f"There is no habit {habit.id}.")
    streak = compute_streak(
        habit, await habits.logs_between(habit.id, dt.date.min, ctx.today), ctx.today
    )
    unit = "day" if streak.unit == "day" else "week"
    return ok(
        f"Logged '{habit.name}' {args.status} for {day}. Streak: {streak.current} {unit}(s).",
        habit_id=habit.id,
        local_date=day,
        status=args.status,
        streak=streak.current,
        streak_unit=streak.unit,
    )


class UpdateHabitArgs(HabitUpdate):
    habit_id: int
    rrule: str | None = Field(default=None, description=RRULE)
    preferred_time: LocalTime | None = Field(default=None, description=HHMM + "; null clears it")


@tool(ToolTier.WRITE, UpdateHabitArgs)
async def update_habit(args: UpdateHabitArgs, ctx: ToolContext) -> ToolResult:
    """Change a habit's name, schedule, target, preferred time or reminder. Pass only changes."""
    changes = HabitUpdate.model_validate(args.model_dump(exclude_unset=True, exclude={"habit_id"}))
    if not changes.changes():
        return fail("invalid_args", "Nothing to change: pass at least one field besides habit_id.")
    habit = await HabitRepo(ctx.session, ctx.user_id).update(args.habit_id, changes)
    if habit is None:
        return fail("not_found", f"There is no habit {args.habit_id}.")
    changed = ", ".join(sorted(changes.changes()))
    return ok(f"Updated habit {habit.id} '{habit.name}': {changed}.", habit=view(habit))
