"""Reminder tools (TOOLS_SPEC section 4, Reminders). Rows only until Phase 4's scheduler."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from sentry_ai.db.enums import ReminderTarget
from sentry_ai.db.repositories import HabitRepo, PlanRepo, RoutineRepo, TaskRepo
from sentry_ai.db.schemas import ReminderCreate, ReminderDTO
from sentry_ai.services.reminders import ReminderError
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool, when
from sentry_ai.tools.tasks import WHEN

REMINDER_VIEW = {"id", "message", "fire_at", "status", "target_kind", "target_id"}


def view(reminder: ReminderDTO) -> dict[str, Any]:
    return reminder.model_dump(include=REMINDER_VIEW)


def _lookup(ctx: ToolContext, kind: ReminderTarget) -> Callable[[int], Awaitable[BaseModel | None]]:
    s, u = ctx.session, ctx.user_id
    getters: dict[str, Callable[[int], Awaitable[BaseModel | None]]] = {
        "task": TaskRepo(s, u).get,
        "habit": HabitRepo(s, u).get,
        "routine": RoutineRepo(s, u).get,
        "plan_block": PlanRepo(s, u).get_block,
    }
    return getters[kind]


class CreateReminderArgs(ToolArgs):
    message: str = Field(min_length=1, max_length=1000, description="what to say when it fires")
    fire_at: AwareDatetime = Field(description=WHEN)
    target_kind: ReminderTarget = Field(
        default="freeform", description="what it is about; freeform needs no target_id"
    )
    target_id: int | None = None


@tool(ToolTier.WRITE, CreateReminderArgs)
async def create_reminder(args: CreateReminderArgs, ctx: ToolContext) -> ToolResult:
    """A plain nudge at a moment, with nothing to track afterwards ("remind me to call mom
    at 7pm", "ping me at 4 to stretch").

    Something to get done by a deadline (submit, finish, pay, prepare) is a task instead:
    create_task with due_at and remind_before_min. Times must be in the future.
    """
    data = ReminderCreate.model_validate(args.model_dump())
    if data.target_id is not None and await _lookup(ctx, data.target_kind)(data.target_id) is None:
        return fail("not_found", f"There is no {data.target_kind} {data.target_id}.")
    try:
        reminder = await ctx.reminder_service().create(data)
    except ReminderError as exc:
        return fail(exc.error, str(exc))
    return ok(
        f"Reminder {reminder.id} set for {when(reminder.fire_at)}: {reminder.message}",
        reminder=view(reminder),
    )


class SnoozeArgs(ToolArgs):
    reminder_id: int
    minutes: int = Field(default=15, ge=1, le=24 * 60)


@tool(ToolTier.WRITE, SnoozeArgs)
async def snooze_reminder(args: SnoozeArgs, ctx: ToolContext) -> ToolResult:
    """Push a reminder back by some minutes from now ("snooze 15", "later")."""
    try:
        reminder = await ctx.reminder_service().snooze(args.reminder_id, args.minutes)
    except ReminderError as exc:
        return fail(exc.error, str(exc))
    return ok(
        f"Snoozed reminder {reminder.id} to {when(reminder.fire_at)}.", reminder=view(reminder)
    )


class AckArgs(ToolArgs):
    reminder_id: int
    action: Literal["done", "skip"]


@tool(ToolTier.WRITE, AckArgs)
async def ack_reminder(args: AckArgs, ctx: ToolContext) -> ToolResult:
    """Answer a reminder with done or skip; also completes/skips the task or habit it is for."""
    try:
        reminder, effect = await ctx.reminder_service().ack(args.reminder_id, args.action)
    except ReminderError as exc:
        return fail(exc.error, str(exc))
    message = f"Reminder {reminder.id} {'done' if args.action == 'done' else 'skipped'}"
    return ok(message + (f"; {effect}." if effect else "."), reminder=view(reminder))


class ReminderIdArgs(ToolArgs):
    reminder_id: int


@tool(ToolTier.WRITE, ReminderIdArgs)
async def cancel_reminder(args: ReminderIdArgs, ctx: ToolContext) -> ToolResult:
    """Cancel a pending reminder so it never fires. The task/habit itself is unchanged."""
    try:
        reminder = await ctx.reminder_service().cancel(args.reminder_id)
    except ReminderError as exc:
        return fail(exc.error, str(exc))
    return ok(f"Cancelled reminder {reminder.id}: {reminder.message}", reminder=view(reminder))
