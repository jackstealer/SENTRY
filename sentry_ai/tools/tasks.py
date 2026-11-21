"""Task tools: one-off to-dos (TOOLS_SPEC section 4, Tasks)."""

from datetime import timedelta
from typing import Any

from pydantic import AwareDatetime, Field

from sentry_ai.db.enums import TaskStatus
from sentry_ai.db.repositories import TaskRepo
from sentry_ai.db.repositories.tasks import OPEN_STATUSES
from sentry_ai.db.schemas import ReminderCreate, TaskCreate, TaskDTO, TaskUpdate
from sentry_ai.services.reminders import ReminderError
from sentry_ai.tools.base import (
    ToolArgs,
    ToolContext,
    ToolResult,
    ToolTier,
    fail,
    ok,
    to_data,
    tool,
    when,
)

WHEN = "ISO 8601 with UTC offset, e.g. 2026-11-27T17:00:00+05:30"
TASK_VIEW = {
    "id", "title", "status", "priority", "due_at", "estimate_min", "project", "notes",
    "scheduled_start",
}  # fmt: skip
CLOSED_TOO: tuple[TaskStatus, ...] = (*OPEN_STATUSES, "done", "skipped")


def view(task: TaskDTO) -> dict[str, Any]:
    """The fields the LLM needs, without bookkeeping columns (saves tokens)."""
    shown: dict[str, Any] = to_data(task.model_dump(include=TASK_VIEW))
    return shown


def _not_found(task_id: int) -> ToolResult:
    return fail("not_found", f"There is no task {task_id}. Use find_task to look it up by title.")


class CreateTaskArgs(ToolArgs):
    title: str = Field(min_length=1, max_length=500)
    due_at: AwareDatetime | None = Field(default=None, description=WHEN)
    priority: int = Field(default=2, ge=1, le=4, description="1 = most important, 4 = least")
    estimate_min: int | None = Field(default=None, gt=0, le=24 * 60, description="minutes of work")
    project: str | None = Field(default=None, description='free-text tag, e.g. "DSA"')
    notes: str | None = None
    remind_before_min: int | None = Field(
        default=None, ge=0, le=7 * 24 * 60, description="remind this many minutes before due_at"
    )


@tool(ToolTier.WRITE, CreateTaskArgs)
async def create_task(args: CreateTaskArgs, ctx: ToolContext) -> ToolResult:
    """Add a one-off to-do, e.g. "submit SE assignment friday 5pm".

    Set remind_before_min to get a reminder before due_at. Not for repeating things: use
    create_habit or create_routine for those.
    """
    if args.remind_before_min is not None and args.due_at is None:
        return fail("invalid_args", "A reminder needs due_at; set it or drop remind_before_min.")
    fields = args.model_dump(exclude={"remind_before_min"})
    task = await TaskRepo(ctx.session, ctx.user_id).create(TaskCreate.model_validate(fields))
    message = f"Created task {task.id} '{task.title}'"
    if task.due_at is not None:
        message += f", due {when(task.due_at)}"
    data: dict[str, Any] = {"task": view(task)}
    if task.due_at is not None and args.remind_before_min is not None:
        fire_at = task.due_at - timedelta(minutes=args.remind_before_min)
        try:
            reminder = await ctx.reminder_service().create(
                ReminderCreate(
                    message=task.title, fire_at=fire_at, target_kind="task", target_id=task.id
                )
            )
        except ReminderError:
            message += "; the reminder time has already passed, so none was set"
        else:
            data["reminder_id"] = reminder.id
            message += f"; reminder at {when(reminder.fire_at)}"
    return ok(message + ".", **data)


class UpdateTaskArgs(ToolArgs):
    task_id: int
    title: str | None = Field(default=None, min_length=1, max_length=500)
    due_at: AwareDatetime | None = Field(default=None, description=WHEN + "; null clears it")
    priority: int | None = Field(default=None, ge=1, le=4)
    estimate_min: int | None = Field(default=None, gt=0, le=24 * 60)
    project: str | None = None
    notes: str | None = None


@tool(ToolTier.WRITE, UpdateTaskArgs)
async def update_task(args: UpdateTaskArgs, ctx: ToolContext) -> ToolResult:
    """Change a task's title, due time, priority, estimate, project or notes.

    Pass only the fields that change. Moving due_at moves its reminders with it.
    Not for finishing or skipping: use complete_task / skip_task.
    """
    changes = TaskUpdate.model_validate(args.model_dump(exclude_unset=True, exclude={"task_id"}))
    if not changes.changes():
        return fail("invalid_args", "Nothing to change: pass at least one field besides task_id.")
    tasks = TaskRepo(ctx.session, ctx.user_id)
    before = await tasks.get(args.task_id)
    if before is None:
        return _not_found(args.task_id)
    task = await tasks.update(args.task_id, changes)
    if task is None:
        return _not_found(args.task_id)
    reminders = ctx.reminder_service()
    if "due_at" in changes.changes() and before.due_at != task.due_at:
        if task.due_at is None:
            await reminders.cancel_for("task", task.id)
        elif before.due_at is not None:
            await reminders.shift_for("task", task.id, task.due_at - before.due_at)
    changed = ", ".join(sorted(changes.changes()))
    return ok(f"Updated task {task.id} '{task.title}': {changed}.", task=view(task))


class TaskIdArgs(ToolArgs):
    task_id: int


@tool(ToolTier.WRITE, TaskIdArgs)
async def complete_task(args: TaskIdArgs, ctx: ToolContext) -> ToolResult:
    """Mark a task done ("finished the SE assignment"). Cancels its pending reminders.

    If you only have a description, call find_task first to get the id.
    """
    tasks = TaskRepo(ctx.session, ctx.user_id)
    task = await tasks.get(args.task_id)
    if task is None:
        return _not_found(args.task_id)
    if task.status == "done":
        return ok(f"Task {task.id} '{task.title}' was already done.", task=view(task))
    done = await tasks.set_status(task.id, "done", ctx.now)
    if done is None:
        return _not_found(args.task_id)
    cancelled = await ctx.reminder_service().cancel_for("task", task.id)
    return ok(
        f"Marked task {task.id} '{task.title}' done.",
        task=view(done),
        reminders_cancelled=cancelled,
    )


class SkipTaskArgs(TaskIdArgs):
    reason: str | None = Field(default=None, max_length=500)


@tool(ToolTier.WRITE, SkipTaskArgs)
async def skip_task(args: SkipTaskArgs, ctx: ToolContext) -> ToolResult:
    """Mark a task skipped: the user decided not to do it. Cancels its pending reminders.

    Not for postponing: use update_task with a new due_at.
    """
    tasks = TaskRepo(ctx.session, ctx.user_id)
    task = await tasks.get(args.task_id)
    if task is None:
        return _not_found(args.task_id)
    if task.status in ("done", "cancelled"):
        return fail("invalid_state", f"Task {task.id} '{task.title}' is already {task.status}.")
    if args.reason:
        notes = f"{task.notes}\n" if task.notes else ""
        await tasks.update(task.id, TaskUpdate(notes=f"{notes}Skipped: {args.reason}"))
    skipped = await tasks.set_status(task.id, "skipped", ctx.now)
    if skipped is None:
        return _not_found(args.task_id)
    cancelled = await ctx.reminder_service().cancel_for("task", task.id)
    return ok(
        f"Skipped task {task.id} '{task.title}'.",
        task=view(skipped),
        reminders_cancelled=cancelled,
    )


class FindTaskArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=200, description="words from the task title")
    include_closed: bool = Field(default=False, description="also search done/skipped tasks")


@tool(ToolTier.READ, FindTaskArgs)
async def find_task(args: FindTaskArgs, ctx: ToolContext) -> ToolResult:
    """Fuzzy-find tasks by title when the user names one loosely ("done with the SE thing").

    Returns the best matches with ids, best first. Open tasks only unless include_closed.
    """
    statuses = CLOSED_TOO if args.include_closed else OPEN_STATUSES
    matches = await TaskRepo(ctx.session, ctx.user_id).find_similar(args.query, statuses=statuses)
    if not matches:
        return ok(f"No task matches {args.query!r}.", matches=[])
    found = [view(m.task) | {"similarity": round(m.similarity, 2)} for m in matches]
    best = matches[0].task
    return ok(f"Best match: task {best.id} '{best.title}'.", matches=found)
