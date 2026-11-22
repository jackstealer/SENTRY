"""Dev-only routes (ARCHITECTURE section 7): run a scheduled job now, e.g. to read a brief.

    curl -X POST http://127.0.0.1:8000/debug/trigger/morning_brief

Only mounted when ENV=dev. `force` (default on) skips the once-a-day claim and quiet hours,
so the same brief can be triggered again and again while working on it.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from sentry_ai.observability.logging import get_logger
from sentry_ai.scheduler import env as job_env
from sentry_ai.scheduler.jobs import (
    evening_review,
    morning_brief,
    routine_materialize,
    weekly_review,
)

log = get_logger(__name__)

JOBS: dict[str, Callable[..., Awaitable[Any]]] = {
    "morning_brief": morning_brief,
    "evening_review": evening_review,
    "weekly_review": weekly_review,
    "routine_materialize": routine_materialize,
}

router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/trigger/{job}")
async def trigger(job: str, force: bool = True) -> dict[str, Any]:
    run = JOBS.get(job)
    if run is None:
        raise HTTPException(404, f"unknown job {job!r}; try one of {sorted(JOBS)}")
    try:
        job_env.current()
    except RuntimeError as exc:
        raise HTTPException(503, "the scheduler is not running in this process") from exc
    log.info("debug_trigger", job=job, force=force)
    return {"job": job, "result": await run(force=force)}
