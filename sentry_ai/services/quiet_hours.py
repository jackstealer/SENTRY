"""Quiet hours (DATA_MODEL section 7): no proactive messages, reminders or nudges inside them."""

from datetime import datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import PreferenceRepo
from sentry_ai.services.intervals import daily
from sentry_ai.services.planner import Prefs
from sentry_ai.services.time import local_date_of


def quiet_until(now: datetime, quiet: tuple[time, time] | None) -> datetime | None:
    """When the quiet window `now` falls in ends, or None if `now` is not quiet."""
    if quiet is None:
        return None
    for span in daily(local_date_of(now), quiet):  # the windows around today, wrapping included
        if span.start <= now < span.end:
            return span.end
    return None


async def quiet_window(session: AsyncSession, user_id: str) -> tuple[time, time] | None:
    return Prefs.from_preferences(await PreferenceRepo(session, user_id).all()).quiet
