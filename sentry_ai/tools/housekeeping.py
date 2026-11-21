"""Housekeeping tools (TOOLS_SPEC section 4). All destructive: each one runs only after the
user confirms the preview (TOOLS_SPEC section 2)."""

import datetime as dt

from pydantic import Field

from sentry_ai.db.repositories import HabitRepo, RoutineRepo, TaskRepo
from sentry_ai.services.time import combine_local, to_utc
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool


class ArchiveArgs(ToolArgs):
    completed_before: dt.date = Field(
        description="local day; archives done tasks finished before it (that day not included)"
    )


def _cutoff(args: ArchiveArgs) -> dt.datetime:
    return to_utc(combine_local(args.completed_before, dt.time.min))


async def _archive_preview(args: ArchiveArgs, ctx: ToolContext) -> str | ToolResult:
    count = await TaskRepo(ctx.session, ctx.user_id).count_done_before(_cutoff(args))
    if count == 0:
        return fail("nothing_to_do", f"No completed tasks finished before {args.completed_before}.")
    return f"This will archive {count} completed task(s) finished before {args.completed_before}."


@tool(ToolTier.DESTRUCTIVE, ArchiveArgs, preview=_archive_preview)
async def archive_tasks(args: ArchiveArgs, ctx: ToolContext) -> ToolResult:
    """Clear old done tasks out of lists ("clean up everything I finished last month").

    Archived tasks stay in the database for stats but no longer show up anywhere.
    Call it straight away: SENTRY shows the user what will happen and asks them to confirm
    before anything changes. Never ask "shall I?" yourself first.
    """
    count = await TaskRepo(ctx.session, ctx.user_id).archive_done_before(_cutoff(args))
    return ok(f"Archived {count} completed task(s).", archived=count)


class HabitIdArgs(ToolArgs):
    habit_id: int


async def _habit_preview(args: HabitIdArgs, ctx: ToolContext) -> str | ToolResult:
    habits = HabitRepo(ctx.session, ctx.user_id)
    habit = await habits.get(args.habit_id)
    if habit is None:
        return fail("not_found", f"There is no habit {args.habit_id}.")
    logs = await habits.logs_between(habit.id, dt.date.min, dt.date.max)
    return f"This will permanently delete habit '{habit.name}' and its {len(logs)} log(s)."


@tool(ToolTier.DESTRUCTIVE, HabitIdArgs, preview=_habit_preview)
async def delete_habit(args: HabitIdArgs, ctx: ToolContext) -> ToolResult:
    """Permanently delete a habit and its whole history. Cancels its pending reminders.

    Call it straight away: SENTRY shows the user what will happen and asks them to confirm
    before anything changes. Never ask "shall I?" yourself first.
    """
    habits = HabitRepo(ctx.session, ctx.user_id)
    habit = await habits.get(args.habit_id)
    if habit is None:
        return fail("not_found", f"There is no habit {args.habit_id}.")
    cancelled = await ctx.reminder_service().cancel_for("habit", habit.id)
    await habits.delete(habit.id)
    return ok(f"Deleted habit '{habit.name}'.", habit_id=habit.id, reminders_cancelled=cancelled)


class RoutineIdArgs(ToolArgs):
    routine_id: int


async def _routine_preview(args: RoutineIdArgs, ctx: ToolContext) -> str | ToolResult:
    routine = await RoutineRepo(ctx.session, ctx.user_id).get(args.routine_id)
    if routine is None:
        return fail("not_found", f"There is no routine {args.routine_id}.")
    return (
        f"This will permanently delete routine '{routine.title}' "
        f"({routine.rrule} at {routine.start_time:%H:%M})."
    )


@tool(ToolTier.DESTRUCTIVE, RoutineIdArgs, preview=_routine_preview)
async def delete_routine(args: RoutineIdArgs, ctx: ToolContext) -> ToolResult:
    """Permanently delete a routine. Cancels its pending reminders. Past plans keep their blocks.

    Call it straight away: SENTRY shows the user what will happen and asks them to confirm
    before anything changes. Never ask "shall I?" yourself first.
    """
    routines = RoutineRepo(ctx.session, ctx.user_id)
    routine = await routines.get(args.routine_id)
    if routine is None:
        return fail("not_found", f"There is no routine {args.routine_id}.")
    cancelled = await ctx.reminder_service().cancel_for("routine", routine.id)
    await routines.delete(routine.id)
    return ok(
        f"Deleted routine '{routine.title}'.", routine_id=routine.id, reminders_cancelled=cancelled
    )
