"""When the daily jobs run: cron triggers from preferences, kept in step when they change."""

from datetime import time
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from sentry_ai.observability.logging import get_logger
from sentry_ai.services.time import parse_hhmm, user_tz

log = get_logger(__name__)

PROACTIVE_GRACE_S = 2 * 3600  # ARCHITECTURE: if the laptop slept through it, still send once

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


class ApsDailySchedule:
    """The running app's DailySchedule: moves the APScheduler jobs."""

    def __init__(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    async def apply(self, prefs: dict[str, dict[str, Any]]) -> list[str]:
        return schedule_daily_jobs(self.scheduler, prefs)
