"""`reminder:{id}` and `nudge:{id}` jobs, and the startup reconcile that keeps them honest.

Reminders and nudges never use the LLM: they are plain templates (PERSONA section 4), so they
go out even when the model is down.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent.context import clock12
from sentry_ai.db.repositories import HabitRepo, PlanRepo, ReminderRepo, RoutineRepo, TaskRepo
from sentry_ai.db.repositories.reminders import PENDING_STATUSES
from sentry_ai.db.schemas import ReminderDTO, ReminderUpdate
from sentry_ai.interfaces.outbound import reminder_buttons
from sentry_ai.observability.logging import bind_trace, get_logger
from sentry_ai.scheduler.env import JobEnv, current
from sentry_ai.scheduler.reminders import (
    NUDGE_AFTER,
    REMINDER_GRACE_S,
    nudge_job_id,
    reminder_job_id,
)
from sentry_ai.services.quiet_hours import quiet_until, quiet_window
from sentry_ai.services.time import local_date_of

log = get_logger(__name__)

LATE_LIMIT = timedelta(minutes=60)  # missed while the app was down: still send if this late
CLOSED_TASK = ("done", "skipped", "cancelled")


@dataclass(frozen=True)
class Target:
    closed: bool  # the task is done, the habit logged, the item deleted: don't nag
    when: datetime | None  # the time the reminder is about (due time, block start)


async def _target(env: JobEnv, session: AsyncSession, reminder: ReminderDTO, today: date) -> Target:
    kind, ref = reminder.target_kind, reminder.target_id
    if ref is None:
        return Target(False, None)
    if kind == "task":
        task = await TaskRepo(session, env.user_id).get(ref)
        closed = task is None or task.status in CLOSED_TASK or task.archived_at is not None
        return Target(closed, None if task is None else task.due_at)
    if kind == "plan_block":
        block = await PlanRepo(session, env.user_id).get_block(ref)
        if block is None or block.outcome in ("done", "skipped"):
            return Target(True, None)
        if block.kind == "task" and block.ref_id is not None:
            task = await TaskRepo(session, env.user_id).get(block.ref_id)
            if task is None or task.status in CLOSED_TASK:
                return Target(True, block.start_at)
        return Target(False, block.start_at)
    if kind == "habit":
        habits = HabitRepo(session, env.user_id)
        habit = await habits.get(ref)
        logged = habit is not None and bool(await habits.logs_between(ref, today, today))
        return Target(habit is None or habit.archived_at is not None or logged, None)
    routine = await RoutineRepo(session, env.user_id).get(ref)
    return Target(routine is None or routine.archived_at is not None, None)


def reminder_text(reminder: ReminderDTO, when: datetime | None, now: datetime) -> str:
    """'⏰ SE assignment at 7:00 AM — in 15 min.' (PERSONA section 4)."""
    late = now - reminder.fire_at > timedelta(seconds=REMINDER_GRACE_S)
    text = f"⏰ {'(late) ' if late else ''}{reminder.message}"
    if when is not None and when > now:
        minutes = max(1, round((when - now).total_seconds() / 60))
        text += f" at {clock12(when)} — in {minutes} min."
    elif when is not None:
        text += f" (was due {clock12(when)})."
    return text


async def fire_reminder(reminder_id: int) -> None:
    env = current()
    bind_trace(job=reminder_job_id(reminder_id))
    now = env.clock()
    async with env.sessions() as session:
        repo = ReminderRepo(session, env.user_id)
        reminder = await repo.get(reminder_id)
        if reminder is None or reminder.status not in PENDING_STATUSES:
            log.info("reminder_skipped", reminder_id=reminder_id,
                     status=None if reminder is None else reminder.status)  # fmt: skip
            return
        target = await _target(env, session, reminder, local_date_of(now))
        if target.closed:
            await repo.update(reminder_id, ReminderUpdate(status="cancelled"))
            log.info("reminder_dropped", reminder_id=reminder_id, reason="target_closed")
            return
        until = quiet_until(now, await quiet_window(session, env.user_id))
        if until is not None:  # never during quiet hours: hold it until they end
            held = await repo.update(reminder_id, ReminderUpdate(fire_at=until))
            if held is not None:
                await env.reminders.schedule(held)
            log.info("reminder_held", reminder_id=reminder_id, until=until)
            return
        message_id = await env.channel.send(
            reminder_text(reminder, target.when, now), reminder_buttons(reminder_id)
        )
        await repo.update(
            reminder_id, ReminderUpdate(status="sent", telegram_message_id=message_id)
        )
    env.reminders.schedule_nudge(reminder_id, now + NUDGE_AFTER)
    log.info(
        "reminder_fired",
        reminder_id=reminder_id,
        late_s=int((now - reminder.fire_at).total_seconds()),
    )


async def fire_nudge(reminder_id: int) -> None:
    """One follow-up, only if the reminder was sent and nobody answered it."""
    env = current()
    bind_trace(job=nudge_job_id(reminder_id))
    async with env.sessions() as session:
        repo = ReminderRepo(session, env.user_id)
        reminder = await repo.get(reminder_id)
        if reminder is None or reminder.status != "sent" or reminder.nudge_sent:
            log.info("nudge_skipped", reminder_id=reminder_id)
            return
        until = quiet_until(env.clock(), await quiet_window(session, env.user_id))
        if until is not None:
            env.reminders.schedule_nudge(reminder_id, until)
            log.info("nudge_held", reminder_id=reminder_id, until=until)
            return
        await env.channel.send(
            f'Still on for "{reminder.message}"? Reply done / snooze / skip.',
            reminder_buttons(reminder_id),
        )
        await repo.update(reminder_id, ReminderUpdate(nudge_sent=True))
    log.info("nudge_sent", reminder_id=reminder_id)


async def reconcile(env: JobEnv) -> dict[str, int]:
    """At startup: every pending row gets its job, missed ones are sent late or expired, and
    jobs whose row is gone or closed are removed."""
    now, counts, aps = env.clock(), Counter[str](), env.reminders
    async with env.sessions() as session:
        repo = ReminderRepo(session, env.user_id)
        keep: set[str] = set()
        for reminder in await repo.list_pending():
            if reminder.fire_at > now:
                await aps.schedule(reminder)
                counts["scheduled"] += 1
            elif now - reminder.fire_at <= LATE_LIMIT:
                await aps.schedule(
                    reminder.model_copy(update={"fire_at": now + timedelta(seconds=1)})
                )
                counts["late"] += 1
            else:
                await repo.update(reminder.id, ReminderUpdate(status="expired"))
                await aps.cancel(reminder.id)
                counts["expired"] += 1
                continue
            keep.add(reminder_job_id(reminder.id))
        for job in env.scheduler.get_jobs():
            kind, _, raw_id = job.id.partition(":")
            if kind not in ("reminder", "nudge") or not raw_id.isdigit() or job.id in keep:
                continue
            if kind == "nudge":
                row = await repo.get(int(raw_id))
                if row is not None and row.status == "sent" and not row.nudge_sent:
                    continue
            env.scheduler.remove_job(job.id)
            counts["orphans_removed"] += 1
    log.info("reminders_reconciled", **counts)
    return dict(counts)
