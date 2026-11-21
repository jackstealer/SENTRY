"""Routine tools: recurring blocks like classes or gym (TOOLS_SPEC section 4, Routines)."""

from datetime import time
from typing import Any

from pydantic import Field

from sentry_ai.db.repositories import RoutineRepo
from sentry_ai.db.schemas import RoutineCreate, RoutineDTO, RoutineUpdate
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool
from sentry_ai.tools.habits import HHMM, RRULE


def view(routine: RoutineDTO) -> dict[str, Any]:
    return routine.model_dump(exclude={"archived_at", "created_at", "updated_at"})


class CreateRoutineArgs(RoutineCreate):
    rrule: str = Field(description=RRULE)
    start_time: time = Field(description=HHMM)
    is_fixed: bool = Field(
        default=True, description="false lets the planner shift it (e.g. lunch); true never"
    )


@tool(ToolTier.WRITE, CreateRoutineArgs)
async def create_routine(args: CreateRoutineArgs, ctx: ToolContext) -> ToolResult:
    """Add a recurring block of time ("college 9 to 2 on weekdays", "gym 6pm Mon-Sat").

    The planner keeps these free. Not for habits to tick off: use create_habit.
    """
    routines = RoutineRepo(ctx.session, ctx.user_id)
    if await routines.get_by_title(args.title) is not None:
        return fail(
            "duplicate", f"A routine titled {args.title!r} already exists; use update_routine."
        )
    routine = await routines.create(RoutineCreate.model_validate(args.model_dump()))
    return ok(f"Added routine {routine.id} '{routine.title}'.", routine=view(routine))


class UpdateRoutineArgs(RoutineUpdate):
    routine_id: int
    rrule: str | None = Field(default=None, description=RRULE)
    start_time: time | None = Field(default=None, description=HHMM)


@tool(ToolTier.WRITE, UpdateRoutineArgs)
async def update_routine(args: UpdateRoutineArgs, ctx: ToolContext) -> ToolResult:
    """Change a routine's time, length, days, active dates or reminder. Pass only changes."""
    changes = RoutineUpdate.model_validate(
        args.model_dump(exclude_unset=True, exclude={"routine_id"})
    )
    if not changes.changes():
        return fail(
            "invalid_args", "Nothing to change: pass at least one field besides routine_id."
        )
    routine = await RoutineRepo(ctx.session, ctx.user_id).update(args.routine_id, changes)
    if routine is None:
        return fail("not_found", f"There is no routine {args.routine_id}.")
    changed = ", ".join(sorted(changes.changes()))
    return ok(f"Updated routine {routine.id} '{routine.title}': {changed}.", routine=view(routine))


class NoArgs(ToolArgs):
    pass


@tool(ToolTier.READ, NoArgs)
async def list_routines(args: NoArgs, ctx: ToolContext) -> ToolResult:
    """List every active routine with its id, schedule (RRULE) and local start time."""
    routines = await RoutineRepo(ctx.session, ctx.user_id).list_active()
    return ok(f"{len(routines)} routine(s).", routines=[view(r) for r in routines])
