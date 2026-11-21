"""The only clock and calendar math in SENTRY: UTC for storage, USER_TZ for "which day".

Never call datetime.now() elsewhere (CONVENTIONS section 2); tests freeze time through this module.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from sentry_ai.config import get_settings

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def user_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().user_tz)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return now_utc().astimezone(user_tz())


def today_local() -> date:
    return now_local().date()


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {value.isoformat()}")
    return value


def to_utc(value: datetime) -> datetime:
    return ensure_aware(value).astimezone(UTC)


def to_local(value: datetime) -> datetime:
    return ensure_aware(value).astimezone(user_tz())


def local_date_of(value: datetime) -> date:
    """The user's calendar day for an instant: 00:30 IST belongs to the new day."""
    return to_local(value).date()


def combine_local(day: date, at: time) -> datetime:
    """Wall-clock time on a local day, as an aware datetime in USER_TZ."""
    return datetime.combine(day, at, tzinfo=user_tz())


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """[start, end) of a local day in UTC: the window behind every "today" query."""
    start = combine_local(day, time.min)
    end = combine_local(day + timedelta(days=1), time.min)
    return to_utc(start), to_utc(end)


def week_start(day: date) -> date:
    """Monday of the ISO week containing `day` (weeks run Monday to Sunday)."""
    return day - timedelta(days=day.weekday())


def parse_hhmm(value: str) -> time:
    match = _HHMM.match(value.strip())
    if match is None:
        raise ValueError(f"expected HH:MM in 24-hour time, got {value!r}")
    return time(int(match[1]), int(match[2]))


def occurrences_on(rule: str, anchor: date, at: time, day: date) -> list[datetime]:
    """Local aware datetimes when `rule`, first anchored on `anchor` at `at`, fires on `day`.

    The rule is expanded in USER_TZ wall time, so "09:00 every weekday" stays 09:00 local.
    """
    if day < anchor:
        return []
    recurrence = rrulestr(rule, dtstart=combine_local(anchor, at))
    start = combine_local(day, time.min)
    end = combine_local(day + timedelta(days=1), time.min)
    hits: list[datetime] = []
    for occurrence in recurrence.xafter(start, inc=True):
        if occurrence >= end:
            break
        hits.append(occurrence)
    return hits


def occurrence_dates(rule: str, anchor: date, first: date, last: date) -> list[date]:
    """Local days in [first, last] (inclusive) on which `rule`, anchored on `anchor`, fires."""
    first = max(first, anchor)
    if last < first:
        return []
    recurrence = rrulestr(rule, dtstart=combine_local(anchor, time.min))
    start = combine_local(first, time.min)
    end = combine_local(last + timedelta(days=1), time.min)
    days = {hit.date() for hit in recurrence.between(start, end, inc=True) if hit < end}
    return sorted(days)
