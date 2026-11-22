"""Daily jobs (ARCHITECTURE section 5): morning brief, evening and weekly review, and the
00:05 routine_materialize. Times come from preferences; reminders live in reminder_jobs.py."""

from datetime import time, timedelta
from functools import partial
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from sentry_ai.agent.context import clock12
from sentry_ai.agent.graph import run_turn
from sentry_ai.agent.prompting import render
from sentry_ai.config import get_settings
from sentry_ai.db.repositories import ReminderRepo
from sentry_ai.db.schemas import ReminderCreate
from sentry_ai.observability.logging import bind_trace, get_logger
from sentry_ai.scheduler.briefs import fallback_text
from sentry_ai.scheduler.env import JobEnv, current
from sentry_ai.scheduler.proactive import PROACTIVE_GRACE_S, send_proactive
from sentry_ai.services.reminders import ReminderService
from sentry_ai.services.routines import expand
from sentry_ai.services.time import local_date_of, parse_hhmm, to_local, user_tz

log = get_logger(__name__)

JOB_REFS = {
    "morning_brief": "sentry_ai.scheduler.jobs:morning_brief",
    "evening_review": "sentry_ai.scheduler.jobs:evening_review",
    "weekly_review": "sentry_ai.scheduler.jobs:weekly_review",
    "routine_materialize": "sentry_ai.scheduler.jobs:routine_materialize",
}
GRACE_S = {
    "morning_brief": PROACTIVE_GRACE_S,
    "evening_review": PROACTIVE_GRACE_S,
    "weekly_review": PROACTIVE_GRACE_S,
    "routine_materialize": 6 * 3600,  # asleep at midnight: still make today's reminders
}
WEEKDAYS = {
    "MO": "mon",
    "TU": "tue",
    "WE": "wed",
    "TH": "thu",
    "FR": "fri",
    "SA": "sat",
    "SU": "sun",
}


# --- proactive messages -------------------------------------------------------------------


async def brief_text(env: JobEnv, trigger: str) -> str:
    """The agent writes it; if the LLM is unavailable, a plain template from the DB does."""
    if env.graph is not None:
        now = to_local(env.clock())
        instruction = render(
            f"proactive/{trigger}",
            weekday=f"{now:%A}",
            date=f"{now:%d %b}".lstrip("0"),
            now_local=clock12(now),
            honorific=get_settings().persona_honorific,
        )
        try:
            result = await run_turn(
                env.graph,
                instruction,
                deps=env.agent_deps(),
                user_id=env.user_id,
                mode="proactive",
                trigger=trigger,
            )
            if not result.degraded and result.reply.strip():
                return result.reply
            reason = "llm_unavailable"
        except Exception as exc:  # whatever broke, the brief still goes out
            reason = type(exc).__name__
        log.warning("proactive_fallback", job=trigger, reason=reason)
    async with env.sessions() as session:
        return await fallback_text(trigger, session=session, user_id=env.user_id, now=env.clock())


async def _proactive(job_key: str, force: bool) -> bool:
    env = current()
    bind_trace(job=job_key)
    build = partial(brief_text, env, job_key)
    return await send_proactive(env, job_key, build, job_ref=JOB_REFS[job_key], force=force)


async def morning_brief(force: bool = False) -> bool:
    return await _proactive("morning_brief", force)


async def evening_review(force: bool = False) -> bool:
    return await _proactive("evening_review", force)


async def weekly_review(force: bool = False) -> bool:
    return await _proactive("weekly_review", force)


# --- routine_materialize ------------------------------------------------------------------


async def routine_materialize(force: bool = False) -> int:
    """Reminder rows (and jobs) for today's routines and timed habits that ask for one.

    Idempotent by data, not by claim: an existing reminder for the same target and time is
    left alone, so running it again (startup, the 00:05 job, the dev trigger) only tops up.
    """
    env = current()
    bind_trace(job="routine_materialize")
    now = env.clock()
    created = 0
    async with env.sessions() as session:
        instances = await expand(local_date_of(now), session=session, user_id=env.user_id)
        wanted: list[tuple[Any, int, str, Any, int | None]] = [
            ("routine", b.routine_id, b.title, b.start_at, b.remind_before_min)
            for b in instances.routines
        ] + [
            ("habit", h.habit_id, h.name, h.preferred_at, h.remind_before_min)
            for h in instances.habits
            if h.preferred_at is not None
        ]
        repo = ReminderRepo(session, env.user_id)
        service = ReminderService(session, env.user_id, env.reminders, now=now)
        for kind, ref, title, start, lead in wanted:
            if lead is None:
                continue
            fire_at = start - timedelta(minutes=lead)
            if fire_at <= now:
                continue
            if any(r.fire_at == fire_at for r in await repo.for_target(kind, ref)):
                continue
            await service.create(
                ReminderCreate(message=title, fire_at=fire_at, target_kind=kind, target_id=ref)
            )
            created += 1
    log.info("routine_materialized", created=created, forced=force)
    return created


# --- schedule from preferences ------------------------------------------------------------


def daily_triggers(prefs: dict[str, dict[str, Any]]) -> dict[str, CronTrigger]:
    """Cron triggers in USER_TZ from wake_time, evening_review_time and weekly_review."""

    def at(key: str, default: str) -> time:
        return parse_hhmm(prefs.get(key, {}).get("time", default))

    weekly = prefs.get("weekly_review", {"day": "SU", "time": "20:00"})
    wake, evening, week = (
        at("wake_time", "07:00"),
        at("evening_review_time", "22:00"),
        parse_hhmm(weekly["time"]),
    )
    tz = user_tz()
    return {
        "morning_brief": CronTrigger(hour=wake.hour, minute=wake.minute, timezone=tz),
        "evening_review": CronTrigger(hour=evening.hour, minute=evening.minute, timezone=tz),
        "weekly_review": CronTrigger(
            day_of_week=WEEKDAYS[weekly["day"]], hour=week.hour, minute=week.minute, timezone=tz
        ),
        "routine_materialize": CronTrigger(hour=0, minute=5, timezone=tz),
    }


def schedule_daily_jobs(scheduler: Any, prefs: dict[str, dict[str, Any]]) -> list[str]:
    """Add missing daily jobs and move ones whose time changed; returns the ids it changed.

    An unchanged job is left as stored: if the app was down at brief time, its overdue run
    still fires once (coalesced) within the grace window instead of being reset to tomorrow.
    """
    changed = []
    for job_id, trigger in daily_triggers(prefs).items():
        existing = scheduler.get_job(job_id)
        if existing is None:
            scheduler.add_job(
                JOB_REFS[job_id], trigger, id=job_id, misfire_grace_time=GRACE_S[job_id]
            )
        elif str(existing.trigger) != str(trigger):
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            continue
        changed.append(job_id)
    if changed:
        log.info("daily_jobs_scheduled", jobs=changed)
    return changed
