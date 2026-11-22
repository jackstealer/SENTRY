"""Plain-template briefs from DB data, sent when the LLM is unavailable (ROADMAP 4.9).

They reuse the read-only overview/stats tools, so the facts match what the agent would see.
Templates: agent/prompts/fallback/*.md (PERSONA section 4 shapes).
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent.context import clock12
from sentry_ai.agent.prompting import render
from sentry_ai.config import get_settings
from sentry_ai.db.repositories import TaskRepo
from sentry_ai.db.repositories.tasks import OPEN_STATUSES
from sentry_ai.services.intervals import Interval
from sentry_ai.services.planner import DEFAULT_TASK_MIN, TaskItem, free_time, order_tasks
from sentry_ai.services.planner_inputs import gather
from sentry_ai.services.time import day_bounds_utc, to_local
from sentry_ai.tools.base import ToolContext
from sentry_ai.tools.query import DayArgs, StatsArgs, get_stats, get_today_overview

FALLBACKS = ("morning_brief", "evening_review", "weekly_review")
DASH = chr(0x2013)  # en dash between times, the way PERSONA writes time ranges


def _bullets(lines: list[str], empty: str) -> str:
    return "\n".join(f"• {line}" for line in lines) if lines else f"• {empty}"


def _greeting(now: datetime) -> dict[str, str]:
    local = to_local(now)
    return {
        "honorific": get_settings().persona_honorific,
        "weekday": f"{local:%A}",
        "date": f"{local:%d %b}".lstrip("0"),
    }


async def _morning(ctx: ToolContext) -> str:
    overview = (await get_today_overview.run(DayArgs(), ctx)).data
    fixed = [
        f"{clock12(b['start_at'])}{DASH}{clock12(b['end_at'])} {b['title']}"
        for b in overview["fixed_blocks"]
    ]
    day_end = day_bounds_utc(ctx.today)[1]
    open_tasks = await TaskRepo(ctx.session, ctx.user_id).query(status=OPEN_STATUSES, limit=50)
    items = [TaskItem(t.id, t.title, t.priority, t.due_at, t.estimate_min or DEFAULT_TASK_MIN)
             for t in open_tasks]  # fmt: skip

    def when(t: TaskItem) -> str:
        if t.due_at is None or t.due_at >= day_end:
            return ""
        return " — overdue" if t.due_at < ctx.now else f" — due {clock12(t.due_at)}"

    ranked = order_tasks(items, urgent_before=day_end)[:3]
    top = [f"{i}. {t.title}{when(t)}" for i, t in enumerate(ranked, 1)]
    habits = ", ".join(h["name"] for h in overview["habits_due"]) or "none"
    overdue = [t.title for t in open_tasks if t.due_at is not None and t.due_at < ctx.now]
    if overdue:
        them = "it" if len(overdue) == 1 else "them"
        observation = f"Overdue: {', '.join(overdue[:3])}. Want me to move {them}?"
    else:
        inputs = await gather(ctx.today, session=ctx.session, user_id=ctx.user_id)
        slots = free_time(ctx.today, inputs.prefs, inputs.busy, now=ctx.now)
        longest: Interval | None = max(slots, key=lambda s: s.minutes, default=None)
        observation = (
            f"Longest free stretch: {clock12(longest.start)}{DASH}{clock12(longest.end)}."
            if longest is not None and longest.minutes >= 60
            else ""
        )
    return render(
        "fallback/morning_brief",
        **_greeting(ctx.now),
        fixed=_bullets(fixed, "nothing fixed"),
        top="\n".join(top) or "Nothing open.",
        habits=habits,
        observation=observation,
    )


async def _evening(ctx: ToolContext) -> str:
    start, end = day_bounds_utc(ctx.today)
    due_today = await TaskRepo(ctx.session, ctx.user_id).query(
        due_after=start, due_before=end, limit=100
    )
    counted = [t for t in due_today if t.status != "cancelled"]
    not_done = [t.title for t in counted if t.status in OPEN_STATUSES]
    overview = (await get_today_overview.run(DayArgs(), ctx)).data
    habits = overview["habits_due"]
    missed = [h["name"] for h in habits if h["logged"] != "done"]
    observation = f"Habits not logged: {', '.join(missed)}." if missed else ""
    if not not_done and not missed and (counted or habits):
        observation = "Clean sweep today."
    return render(
        "fallback/evening_review",
        date=_greeting(ctx.now)["date"],
        tasks_done=sum(t.status == "done" for t in counted),
        tasks_total=len(counted),
        habits_done=sum(h["logged"] == "done" for h in habits),
        habits_total=len(habits),
        not_done=", ".join(not_done),
        observation=observation,
    )


async def _weekly(ctx: ToolContext) -> str:
    stats = (await get_stats.run(StatsArgs(range="week"), ctx)).data
    return render(
        "fallback/weekly_review",
        start=f"{datetime.fromisoformat(stats['from']):%d %b}".lstrip("0"),
        end=f"{datetime.fromisoformat(stats['to']):%d %b}".lstrip("0"),
        tasks_done=stats["tasks_done"],
        tasks_due=stats["tasks_due"],
        task_pct=stats["task_completion_pct"],
        habit_pct=stats["habit_pct"],
        rollovers=stats["rollovers"],
        plan_done=stats["plan_blocks_done"],
        plan_blocks=stats["plan_blocks"],
    )


async def fallback_text(trigger: str, *, session: AsyncSession, user_id: str, now: datetime) -> str:
    """The templated version of a proactive message, built only from the database."""
    ctx = ToolContext(user_id=user_id, trace_id="fallback", now=now, session=session)
    builders = {"morning_brief": _morning, "evening_review": _evening, "weekly_review": _weekly}
    if trigger not in builders:
        raise ValueError(f"no fallback template for {trigger!r}")
    return await builders[trigger](ctx)
