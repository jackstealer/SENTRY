"""Daily job times follow preferences. Tools call DailySchedule after saving one of
SCHEDULE_KEYS; the running app plugs in the APScheduler version (scheduler/jobs.py)."""

from typing import Any, Protocol, runtime_checkable

from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)

SCHEDULE_KEYS = frozenset({"wake_time", "evening_review_time", "weekly_review"})


@runtime_checkable
class DailySchedule(Protocol):
    async def apply(self, prefs: dict[str, dict[str, Any]]) -> list[str]:
        """Move daily jobs to the times in `prefs`; returns the job ids that changed."""
        ...


class NoopDailySchedule:
    """Outside the running app (CLI, tests): the next app start applies the stored times."""

    async def apply(self, prefs: dict[str, dict[str, Any]]) -> list[str]:
        log.info("daily_schedule_deferred")
        return []
