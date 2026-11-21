from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
import time_machine

from sentry_ai.config import get_settings
from sentry_ai.services import time as t

IST_OFFSET = timedelta(hours=5, minutes=30)
WEEKDAYS = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
# 2026-11-21 is a Saturday, 22nd Sunday, 23rd Monday


@pytest.fixture(autouse=True)
def ist(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("USER_TZ", "Asia/Kolkata")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@time_machine.travel(datetime(2026, 11, 21, 18, 20, tzinfo=UTC), tick=False)
def test_2350_ist_is_still_the_same_local_day() -> None:
    assert t.now_utc() == datetime(2026, 11, 21, 18, 20, tzinfo=UTC)
    local = t.now_local()
    assert (local.hour, local.minute, local.utcoffset()) == (23, 50, IST_OFFSET)
    assert t.today_local() == date(2026, 11, 21)


@time_machine.travel(datetime(2026, 11, 21, 19, 0, tzinfo=UTC), tick=False)
def test_0030_ist_is_already_the_next_local_day() -> None:
    # UTC still says the 21st; the user's day is the 22nd
    assert t.now_utc().date() == date(2026, 11, 21)
    assert (t.now_local().hour, t.now_local().minute) == (0, 30)
    assert t.today_local() == date(2026, 11, 22)
    assert t.local_date_of(t.now_utc()) == date(2026, 11, 22)


def test_local_day_flips_exactly_at_1830_utc() -> None:
    flip = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)
    assert t.local_date_of(flip - timedelta(microseconds=1)) == date(2026, 11, 21)
    assert t.local_date_of(flip) == date(2026, 11, 22)


def test_day_bounds_are_the_local_day_in_utc() -> None:
    start, end = t.day_bounds_utc(date(2026, 11, 22))
    assert start == datetime(2026, 11, 21, 18, 30, tzinfo=UTC)
    assert end == datetime(2026, 11, 22, 18, 30, tzinfo=UTC)
    assert start.tzinfo is UTC and end - start == timedelta(days=1)


@pytest.mark.parametrize("fn", [t.to_utc, t.to_local, t.local_date_of, t.ensure_aware])
def test_naive_datetimes_are_rejected(fn: object) -> None:
    with pytest.raises(ValueError, match="naive"):
        fn(datetime(2026, 11, 21, 23, 50))  # type: ignore[operator]


def test_conversions_keep_the_instant() -> None:
    local = t.combine_local(date(2026, 11, 21), time(23, 50))
    assert local.utcoffset() == IST_OFFSET
    assert t.to_utc(local) == datetime(2026, 11, 21, 18, 20, tzinfo=UTC)
    assert t.to_local(t.to_utc(local)) == local


def test_user_tz_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER_TZ", "Europe/London")
    get_settings.cache_clear()
    assert t.user_tz().key == "Europe/London"
    with time_machine.travel(datetime(2026, 11, 21, 23, 30, tzinfo=UTC), tick=False):
        assert t.today_local() == date(2026, 11, 21)  # London: still the 21st


@pytest.mark.parametrize(("raw", "parsed"), [("06:30", time(6, 30)), (" 23:59 ", time(23, 59))])
def test_parse_hhmm(raw: str, parsed: time) -> None:
    assert t.parse_hhmm(raw) == parsed


@pytest.mark.parametrize("bad", ["6:30", "24:00", "12:60", "noon", "", "06:30:00"])
def test_parse_hhmm_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError, match="HH:MM"):
        t.parse_hhmm(bad)


def test_week_start_is_monday() -> None:
    assert t.week_start(date(2026, 11, 22)) == date(2026, 11, 16)  # Sunday -> previous Monday
    assert t.week_start(date(2026, 11, 23)) == date(2026, 11, 23)  # Monday -> itself


def test_occurrences_on_weekday_vs_weekend() -> None:
    anchor = date(2026, 11, 1)
    assert t.occurrences_on(WEEKDAYS, anchor, time(9, 0), date(2026, 11, 21)) == []
    [monday] = t.occurrences_on(WEEKDAYS, anchor, time(9, 0), date(2026, 11, 23))
    assert monday == t.combine_local(date(2026, 11, 23), time(9, 0))
    assert t.to_utc(monday) == datetime(2026, 11, 23, 3, 30, tzinfo=UTC)


def test_occurrences_on_respects_the_anchor() -> None:
    assert t.occurrences_on("FREQ=DAILY", date(2026, 12, 1), time(7), date(2026, 11, 30)) == []
    every_other = "FREQ=DAILY;INTERVAL=2"
    anchor = date(2026, 11, 1)
    assert t.occurrences_on(every_other, anchor, time(7), date(2026, 11, 21))  # day 20 after
    assert t.occurrences_on(every_other, anchor, time(7), date(2026, 11, 22)) == []


def test_late_night_occurrence_stays_on_its_local_day() -> None:
    # 23:50 IST is 18:20 UTC on the same date; it must not leak into the next local day
    rule, anchor = "FREQ=DAILY", date(2026, 11, 1)
    [hit] = t.occurrences_on(rule, anchor, time(23, 50), date(2026, 11, 21))
    assert hit.date() == date(2026, 11, 21)
    [next_day] = t.occurrences_on(rule, anchor, time(23, 50), date(2026, 11, 22))
    assert next_day.date() == date(2026, 11, 22)


def test_occurrence_dates_lists_due_days_in_range() -> None:
    days = t.occurrence_dates(WEEKDAYS, date(2026, 11, 1), date(2026, 11, 19), date(2026, 11, 24))
    assert days == [date(2026, 11, d) for d in (19, 20, 23, 24)]
    # range before the anchor is clipped; reversed range is empty
    clipped = t.occurrence_dates(
        "FREQ=DAILY", date(2026, 11, 20), date(2026, 11, 1), date(2026, 11, 21)
    )
    assert clipped == [date(2026, 11, 20), date(2026, 11, 21)]
    assert (
        t.occurrence_dates("FREQ=DAILY", date(2026, 11, 1), date(2026, 11, 5), date(2026, 11, 4))
        == []
    )
