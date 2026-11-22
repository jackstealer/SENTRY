"""Sending a proactive message (brief, review) exactly once per local day, never in quiet hours.

A claim in `proactive_runs` is committed before the send, so restarts, misfires and a double
trigger cannot send twice; if the send fails the claim is released so a retry can still go.
"""

from collections.abc import Awaitable, Callable

from apscheduler.triggers.date import DateTrigger

from sentry_ai.db.repositories import SystemRepo
from sentry_ai.observability.logging import get_logger
from sentry_ai.scheduler.env import JobEnv
from sentry_ai.services.quiet_hours import quiet_until, quiet_window
from sentry_ai.services.time import local_date_of

log = get_logger(__name__)

PROACTIVE_GRACE_S = 2 * 3600  # ARCHITECTURE: if the laptop slept through it, still send once


async def send_proactive(
    env: JobEnv,
    job_key: str,
    build: Callable[[], Awaitable[str]],
    *,
    job_ref: str,
    force: bool = False,
) -> bool:
    """True if the message went out. `force` (the dev trigger) skips quiet hours and the claim."""
    now = env.clock()
    day = local_date_of(now)
    if not force:
        async with env.sessions() as session:
            until = quiet_until(now, await quiet_window(session, env.user_id))
        if until is not None:
            if local_date_of(until) != day:  # e.g. an evening review at 23:45: stale by morning
                log.info("proactive_skipped", job=job_key, reason="quiet_hours")
                return False
            env.scheduler.add_job(
                job_ref,
                DateTrigger(run_date=until),
                id=f"{job_key}:held",
                replace_existing=True,
                misfire_grace_time=PROACTIVE_GRACE_S,
            )
            log.info("proactive_held", job=job_key, until=until)
            return False
        async with env.sessions() as session:
            claimed = await SystemRepo(session, env.user_id).claim_proactive_run(job_key, day)
        if not claimed:
            log.info("proactive_skipped", job=job_key, reason="already_sent")
            return False
    try:
        text = await build()
        await env.channel.send(text)
    except Exception:
        if not force:
            async with env.sessions() as session:
                await SystemRepo(session, env.user_id).release_proactive_run(job_key, day)
        raise
    log.info("proactive_sent", job=job_key, forced=force)
    return True
