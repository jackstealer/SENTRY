"""Read-only overview tools (TOOLS_SPEC section 4, Overview / query)."""

import datetime as dt
from datetime import timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, Field

from sentry_ai.db.enums import TaskStatus
from sentry_ai.db.repositories import (
    CalendarRepo,
    HabitRepo,
    PlanRepo,
    ReminderRepo,
    TaskRepo,
)
from sentry_ai.db.repositories.tasks import OPEN_STATUSES
from sentry_ai.db.schemas import HabitDTO
from sentry_ai.services.planner import Busy, free_time, hhmm
from sentry_ai.services.planner_inputs import gather
from sentry_ai.services.routines import expand
from sentry_ai.services.streaks import compute_streak
from sentry_ai.services.time import day_bounds_utc, local_date_of, occurrence_dates, week_start
from sentry_ai.tools import reminders, tasks
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool
from sentry_ai.tools.tasks import WHEN

DATE = "local day, YYYY-MM-DD; defaults to today"


def _pct(part: int, whole: int) -> int | None:
    return None if whole == 0 else round(100 * part / whole)


class DayArgs(ToolArgs):
    date: dt.date | None = Field(default=None, description=DATE)


@tool(ToolTier.READ, DayArgs)
async def get_today_overview(args: DayArgs, ctx: ToolContext) -> ToolResult:
    """Everything about one day: fixed blocks, calendar events, the confirmed plan, tasks due
    (overdue included), habits due with today's status, and pending reminders.

    Start here for "what's on today/tomorrow" and before planning.
    """
    day = args.date or ctx.today
    start, end = day_bounds_utc(day)
    instances = await expand(day, session=ctx.session, user_id=ctx.user_id)
    events = await CalendarRepo(ctx.session, ctx.user_id).between(start, end)
    plan = await PlanRepo(ctx.session, ctx.user_id).get_confirmed(day)
    due = await TaskRepo(ctx.session, ctx.user_id).query(status=OPEN_STATUSES, due_before=end)
    habit_repo = HabitRepo(ctx.session, ctx.user_id)
    habits = []
    for habit in instances.habits:
        logs = await habit_repo.logs_between(habit.habit_id, day, day)
        habits.append(
            {"habit_id": habit.habit_id, "name": habit.name, "preferred_at": habit.preferred_at,
             "logged": logs[0].status if logs else None}
        )  # fmt: skip
    pending = [
        r for r in await ReminderRepo(ctx.session, ctx.user_id).list_pending(before=end)
        if r.fire_at >= start
    ]  # fmt: skip
    message = (
        f"{day:%a %d %b}: {len(instances.routines)} routine block(s), {len(events)} event(s), "
        f"{len(due)} task(s) due, {len(habits)} habit(s) due, {len(pending)} reminder(s); "
        + (f"plan {plan.id} confirmed." if plan else "no confirmed plan.")
    )
    shown_plan = None
    if plan is not None:
        fields = {"id", "start_at", "end_at", "kind", "title", "outcome"}
        shown_plan = {
            "plan_id": plan.id,
            "blocks": [b.model_dump(include=fields) for b in plan.blocks],
        }
    return ok(
        message,
        date=day,
        fixed_blocks=[
            b.model_dump(include={"routine_id", "title", "start_at", "end_at", "is_fixed"})
            for b in instances.routines
        ],
        calendar_events=[
            e.model_dump(include={"title", "start_at", "end_at", "all_day"}) for e in events
        ],
        plan=shown_plan,
        tasks_due=[tasks.view(t) for t in due],
        habits_due=habits,
        reminders=[reminders.view(r) for r in pending],
    )


class ListTasksArgs(ToolArgs):
    status: TaskStatus | None = Field(default=None, description="omit for open tasks")
    due_before: AwareDatetime | None = Field(default=None, description=WHEN)
    project: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


@tool(ToolTier.READ, ListTasksArgs)
async def list_tasks(args: ListTasksArgs, ctx: ToolContext) -> ToolResult:
    """List tasks, most urgent first. Without `status`, only open (todo/in-progress) tasks."""
    found = await TaskRepo(ctx.session, ctx.user_id).query(
        status=[args.status] if args.status else OPEN_STATUSES,
        due_before=args.due_before,
        project=args.project,
        limit=args.limit,
    )
    return ok(f"{len(found)} task(s).", tasks=[tasks.view(t) for t in found])


class HabitsStatusArgs(ToolArgs):
    period: Literal["day", "week"] = "day"


def _expected(habit: HabitDTO, first: dt.date, last: dt.date) -> int:
    """Times the habit should be done in [first, last]: due days, or the weekly target."""
    if habit.period == "week":
        return habit.target_per_period
    anchor = local_date_of(habit.created_at)
    return habit.target_per_period * len(occurrence_dates(habit.rrule, anchor, first, last))


@tool(ToolTier.READ, HabitsStatusArgs)
async def get_habits_status(args: HabitsStatusArgs, ctx: ToolContext) -> ToolResult:
    """Each habit's progress today (or this Mon-Sun week) against its target, with its streak."""
    today = ctx.today
    first, last = (today, today) if args.period == "day" else (
        week_start(today), week_start(today) + timedelta(days=6)
    )  # fmt: skip
    repo = HabitRepo(ctx.session, ctx.user_id)
    rows: list[dict[str, Any]] = []
    for habit in await repo.list_active():
        logs = await repo.logs_between(habit.id, dt.date.min, today)
        done = sum(1 for log in logs if first <= log.local_date <= last and log.status == "done")
        streak = compute_streak(habit, logs, today)
        target = None if args.period == "day" and habit.period == "week" else _expected(
            habit, first, last
        )  # fmt: skip
        rows.append(
            {"habit_id": habit.id, "name": habit.name, "period": habit.period, "done": done,
             "target": target, "streak": streak.current, "streak_unit": streak.unit}
        )  # fmt: skip
    summary = "; ".join(
        f"{r['name']} {r['done']}/{r['target'] if r['target'] is not None else '-'}"
        f" (streak {r['streak']} {r['streak_unit']})"
        for r in rows
    )
    return ok(summary or "No habits tracked yet.", period=args.period, habits=rows)


class FreeSlotsArgs(DayArgs):
    min_duration_min: int = Field(default=30, ge=5, le=24 * 60)


@tool(ToolTier.READ, FreeSlotsArgs)
async def get_free_slots(args: FreeSlotsArgs, ctx: ToolContext) -> ToolResult:
    """Free time on a day, respecting fixed blocks, the confirmed plan, quiet hours and
    no-tasks-before / sleep time. Use for "when am I free tomorrow?"."""
    day = args.date or ctx.today
    if day < ctx.today:
        return fail("invalid_args", f"{day} is already over.")
    inputs = await gather(day, session=ctx.session, user_id=ctx.user_id)
    plan = await PlanRepo(ctx.session, ctx.user_id).get_confirmed(day)
    planned = [
        Busy(b.start_at, b.end_at, b.title, "routine", b.ref_id) for b in plan.blocks
    ] if plan else []  # fmt: skip
    spans = free_time(day, inputs.prefs, [*inputs.busy, *planned], now=ctx.now)
    slots = [s for s in spans if s.minutes >= args.min_duration_min]
    listed = ", ".join(f"{hhmm(s.start)}-{hhmm(s.end)}" for s in slots)
    return ok(
        f"{len(slots)} free slot(s) on {day:%a %d %b}" + (f": {listed}." if slots else "."),
        date=day,
        slots=[{"start_at": s.start, "end_at": s.end, "minutes": s.minutes} for s in slots],
        total_minutes=sum(s.minutes for s in slots),
    )


class StatsArgs(ToolArgs):
    range: Literal["week", "month"] = "week"


@tool(ToolTier.READ, StatsArgs)
async def get_stats(args: StatsArgs, ctx: ToolContext) -> ToolResult:
    """How the last 7 (week) or 30 (month) days went: task completion, habit hit rate,
    rollovers and how much of the confirmed plans got done. For reviews."""
    today = ctx.today
    first = today - timedelta(days=(7 if args.range == "week" else 30) - 1)
    start, end = day_bounds_utc(first)[0], day_bounds_utc(today)[1]
    # shortcut: 1000 tasks per window is plenty for one person
    due = await TaskRepo(ctx.session, ctx.user_id).query(
        due_after=start, due_before=end, limit=1000
    )
    counted = [t for t in due if t.status != "cancelled"]
    done = sum(t.status == "done" for t in counted)

    habit_repo = HabitRepo(ctx.session, ctx.user_id)
    hit = expected = 0
    for habit in await habit_repo.list_active():
        logs = await habit_repo.logs_between(habit.id, first, today)
        if habit.period == "week":  # 7 days = 1 week, 30 days ~ 4 weeks of target
            want = habit.target_per_period * round(((today - first).days + 1) / 7)
        else:
            want = _expected(habit, first, today)
        got = sum(log.status == "done" for log in logs)
        hit, expected = hit + min(got, want), expected + want
    plans = PlanRepo(ctx.session, ctx.user_id)
    blocks = done_blocks = 0
    day = first
    while day <= today:
        plan = await plans.get_confirmed(day)
        for block in plan.blocks if plan else []:
            if block.kind in ("task", "habit") and block.end_at <= ctx.now:
                blocks += 1
                done_blocks += block.outcome == "done"
        day += timedelta(days=1)
    rollovers = sum(t.rolled_over_count for t in counted)
    stats = {
        "from": first, "to": today, "tasks_due": len(counted), "tasks_done": done,
        "task_completion_pct": _pct(done, len(counted)), "habit_pct": _pct(hit, expected),
        "rollovers": rollovers, "plan_blocks": blocks, "plan_blocks_done": done_blocks,
        "plan_adherence_pct": _pct(done_blocks, blocks),
    }  # fmt: skip
    return ok(
        f"{first:%d %b}-{today:%d %b}: tasks {done}/{len(counted)} done, habits "
        f"{stats['habit_pct'] if expected else '-'}%, plan {done_blocks}/{blocks} blocks done, "
        f"{rollovers} rollover(s).",
        **stats,
    )
