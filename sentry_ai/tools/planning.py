"""Planning tools: the LLM decides what matters; services/planner.py decides where it fits."""

import datetime as dt
from datetime import time, timedelta
from typing import Any

from pydantic import AwareDatetime, Field, model_validator

from sentry_ai.db.repositories import PlanRepo, PreferenceRepo, RoutineRepo, TaskRepo
from sentry_ai.db.schemas import (
    PlanBlockCreate,
    PlanBlockDTO,
    PlanBlockUpdate,
    ReminderCreate,
    TaskUpdate,
)
from sentry_ai.services.intervals import Interval, daily
from sentry_ai.services.planner import Prefs, check_slot, hhmm, plan_day
from sentry_ai.services.planner_inputs import gather
from sentry_ai.services.time import day_bounds_utc
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool
from sentry_ai.tools.habits import HHMM
from sentry_ai.tools.tasks import WHEN

BLOCK_VIEW = {"id", "start_at", "end_at", "kind", "ref_id", "title"}


def view(block: PlanBlockDTO) -> dict[str, Any]:
    return block.model_dump(include=BLOCK_VIEW)


class Window(ToolArgs):
    start: time = Field(description=HHMM)
    end: time = Field(description=HHMM)

    @model_validator(mode="after")
    def _not_empty(self) -> "Window":
        if self.start == self.end:
            raise ValueError("a window needs different start and end times")
        return self


class ProposeArgs(ToolArgs):
    date: dt.date | None = Field(default=None, description="local day; defaults to today")
    available_windows: list[Window] | None = Field(
        default=None, description="only plan inside these local windows"
    )
    focus: str | None = Field(
        default=None,
        max_length=100,
        description="tasks whose title or project mention this go first",
    )
    task_order: list[int] = Field(
        default_factory=list,
        description="task ids in the order that matters today; the rest follow by urgency",
    )


@tool(ToolTier.WRITE, ProposeArgs)
async def propose_day_plan(args: ProposeArgs, ctx: ToolContext) -> ToolResult:
    """Draft a day plan: routines and calendar stay put; tasks and habits fill the free time.

    You decide what matters (task_order, focus); the planner decides the times, so never
    invent timestamps yourself. Show the draft, then confirm_day_plan if the user agrees.
    """
    day = args.date or ctx.today
    if day < ctx.today:
        return fail("invalid_args", f"{day} is already over; plan today or a later day.")
    inputs = await gather(day, session=ctx.session, user_id=ctx.user_id)
    windows = None
    if args.available_windows is not None:
        windows = [span for w in args.available_windows for span in daily(day, (w.start, w.end))]
    draft = plan_day(
        day,
        inputs.prefs,
        busy=inputs.busy,
        flexible=inputs.flexible,
        tasks=inputs.tasks,
        now=ctx.now,
        windows=windows,
        task_order=args.task_order,
        focus=args.focus,
    )
    whole_day = Interval(*day_bounds_utc(day))
    blocks = [
        PlanBlockCreate(start_at=b.start, end_at=b.end, kind=b.kind, ref_id=b.ref_id, title=b.title)
        for b in inputs.busy
        if Interval(b.start, b.end).overlaps(whole_day)
    ] + [
        PlanBlockCreate(start_at=p.start, end_at=p.end, kind=p.kind, ref_id=p.ref_id, title=p.title)
        for p in draft.placed
    ]
    plan = await PlanRepo(ctx.session, ctx.user_id).create_draft(day, None, blocks)
    placed = len(draft.placed)
    message = f"Draft plan {plan.id} for {day:%a %d %b}: {placed} item(s) placed"
    message += f" around {len(blocks) - placed} fixed block(s)."
    if draft.unplaced:
        message += f" {len(draft.unplaced)} item(s) do not fit ({draft.overcommitted_by_min} min)."
    if draft.deferred:
        message += f" {len(draft.deferred)} task(s) left for another day (max_tasks_per_day)."
    if draft.conflicts:
        message += f" Clashes: {'; '.join(draft.conflicts)}."
    return ok(
        message,
        plan_id=plan.id,
        local_date=day,
        blocks=[view(b) for b in plan.blocks],
        unplaced=draft.unplaced,
        deferred=draft.deferred,
        conflicts=draft.conflicts,
        overcommitted_by_min=draft.overcommitted_by_min,
        free_minutes=draft.free_minutes,
    )


class ConfirmArgs(ToolArgs):
    plan_id: int
    sync_to_calendar: bool = True


@tool(ToolTier.WRITE, ConfirmArgs)
async def confirm_day_plan(args: ConfirmArgs, ctx: ToolContext) -> ToolResult:
    """Lock in a draft plan once the user agrees to it.

    Replaces any earlier plan for that day and sets a reminder before each task and habit
    block (default_reminder_min ahead).
    """
    plans, tasks = PlanRepo(ctx.session, ctx.user_id), TaskRepo(ctx.session, ctx.user_id)
    plan = await plans.get(args.plan_id)
    if plan is None:
        return fail("not_found", f"There is no plan {args.plan_id}.")
    if plan.status == "confirmed":
        return ok(f"Plan {plan.id} is already confirmed.", plan_id=plan.id)
    if plan.status == "superseded":
        return fail("invalid_state", f"Plan {plan.id} was replaced by a newer one.")
    if plan.local_date < ctx.today:
        return fail("invalid_state", f"Plan {plan.id} is for {plan.local_date}, which is over.")
    reminders = ctx.reminder_service()
    previous = await plans.get_confirmed(plan.local_date)
    for block in previous.blocks if previous else []:
        await reminders.cancel_for("plan_block", block.id)
        if block.kind == "task" and block.ref_id is not None:
            await tasks.update(block.ref_id, TaskUpdate(scheduled_start=None))
    confirmed = await plans.confirm(plan.id)
    if confirmed is None:
        return fail("not_found", f"There is no plan {args.plan_id}.")
    prefs = Prefs.from_preferences(await PreferenceRepo(ctx.session, ctx.user_id).all())
    lead = timedelta(minutes=prefs.reminder_min)
    created = 0
    for block in confirmed.blocks:
        if block.kind not in ("task", "habit"):
            continue
        if block.kind == "task" and block.ref_id is not None:
            await tasks.update(block.ref_id, TaskUpdate(scheduled_start=block.start_at))
        if block.start_at - lead > ctx.now:
            await reminders.create(
                ReminderCreate(
                    message=f"{block.title} at {hhmm(block.start_at)}",
                    fire_at=block.start_at - lead,
                    target_kind="plan_block",
                    target_id=block.id,
                )
            )
            created += 1
    return ok(
        f"Confirmed plan {plan.id} for {plan.local_date:%a %d %b}; {created} reminder(s) set.",
        plan_id=plan.id,
        reminders_created=created,
        replaced_plan_id=previous.id if previous else None,
        calendar_synced=False,  # writing to Google Calendar arrives with Phase 6
    )


class MoveBlockArgs(ToolArgs):
    block_id: int
    new_start: AwareDatetime = Field(description=WHEN)


@tool(ToolTier.WRITE, MoveBlockArgs)
async def move_block(args: MoveBlockArgs, ctx: ToolContext) -> ToolResult:
    """Move one plan block to a new start time, same length ("move DSA to 6pm").

    Its reminder moves too. Fixed routines and calendar events cannot be moved this way.
    """
    plans = PlanRepo(ctx.session, ctx.user_id)
    block = await plans.get_block(args.block_id)
    plan = None if block is None else await plans.get(block.plan_id)
    if block is None or plan is None:
        return fail("not_found", f"There is no plan block {args.block_id}.")
    if plan.status == "superseded":
        return fail("invalid_state", f"Block {block.id} belongs to a plan that was replaced.")
    if block.kind == "calendar_event":
        return fail("invalid_state", f"'{block.title}' is a calendar event; it stays where it is.")
    if block.kind == "routine" and block.ref_id is not None:
        routine = await RoutineRepo(ctx.session, ctx.user_id).get(block.ref_id)
        if routine is not None and routine.is_fixed:
            return fail(
                "invalid_state",
                f"'{block.title}' is a fixed routine; change it with update_routine.",
            )
    if args.new_start < ctx.now:
        return fail("invalid_time", "That time has already passed.")
    slot = Interval(args.new_start, args.new_start + (block.end_at - block.start_at))
    prefs = Prefs.from_preferences(await PreferenceRepo(ctx.session, ctx.user_id).all())
    others = [(b.title, Interval(b.start_at, b.end_at)) for b in plan.blocks if b.id != block.id]
    problem = check_slot(plan.local_date, prefs, slot, others)
    if problem is not None:
        return fail("conflict", f"Can't move '{block.title}' there: {problem}.")
    moved = await plans.update_block(
        block.id, PlanBlockUpdate(start_at=slot.start, end_at=slot.end)
    )
    if moved is None:
        return fail("not_found", f"There is no plan block {args.block_id}.")
    await ctx.reminder_service().shift_for("plan_block", block.id, slot.start - block.start_at)
    if plan.status == "confirmed" and block.kind == "task" and block.ref_id is not None:
        await TaskRepo(ctx.session, ctx.user_id).update(
            block.ref_id, TaskUpdate(scheduled_start=slot.start)
        )
    return ok(f"Moved '{block.title}' to {hhmm(slot.start)}-{hhmm(slot.end)}.", block=view(moved))
