from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import time_machine

from sentry_ai.config import get_settings
from sentry_ai.db.schemas import HabitDTO, HabitLogDTO
from sentry_ai.services import time as t
from sentry_ai.services.streaks import compute_streak

TODAY = date(2026, 11, 25)  # Wednesday
CREATED = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def ist(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("USER_TZ", "Asia/Kolkata")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def habit(rrule: str = "FREQ=DAILY", period: str = "day", target: int = 1, **kw: Any) -> HabitDTO:
    fields = {
        "id": 1,
        "name": "DSA practice",
        "rrule": rrule,
        "target_per_period": target,
        "period": period,
        "preferred_time": None,
        "remind_before_min": None,
        "archived_at": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return HabitDTO.model_validate(fields | kw)


def logs(*entries: tuple[int, str], base: date = TODAY) -> list[HabitLogDTO]:
    """entries are (days_before_base, status)."""
    return [
        HabitLogDTO(
            id=i,
            habit_id=1,
            local_date=base - timedelta(days=ago),
            status=status,  # type: ignore[arg-type]
            note=None,
            logged_at=CREATED,
        )
        for i, (ago, status) in enumerate(entries, start=1)
    ]


def days(h: HabitDTO, entries: list[HabitLogDTO], today: date = TODAY) -> int:
    streak = compute_streak(h, entries, today)
    assert streak.unit == h.period
    return streak.current


# --- day habits -----------------------------------------------------------------------


def test_consecutive_days_including_today() -> None:
    assert days(habit(), logs((0, "done"), (1, "done"), (2, "done"), (3, "done"))) == 4


def test_today_not_logged_yet_does_not_break() -> None:
    assert days(habit(), logs((1, "done"), (2, "done"), (3, "done"))) == 3


def test_a_gap_day_breaks_the_streak() -> None:
    assert days(habit(), logs((0, "done"), (2, "done"), (3, "done"))) == 1
    assert days(habit(), logs((1, "done"), (2, "done"), (4, "done"), (5, "done"))) == 2


def test_skipped_is_neutral() -> None:
    assert days(habit(), logs((0, "done"), (1, "skipped"), (2, "done"), (3, "done"))) == 3
    assert days(habit(), logs((0, "skipped"), (1, "done"))) == 1


def test_missed_breaks_even_when_logged_explicitly() -> None:
    assert days(habit(), logs((0, "done"), (1, "missed"), (2, "done"))) == 1
    assert days(habit(), logs((0, "missed"), (1, "done"))) == 0


def test_only_due_days_matter() -> None:
    weekdays = habit(rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR")
    # Mon 23 + Fri 20 done; Sat/Sun are not due so they cannot break it; Tue 24 done
    entries = logs((5, "done"), (2, "done"), (1, "done"))
    assert days(weekdays, entries) == 3  # today (Wed) not logged yet
    # done on a Saturday does not count for a weekday-only habit
    assert days(weekdays, logs((4, "done"))) == 0


def test_streak_never_reaches_before_the_habit_existed() -> None:
    fresh = habit(created_at=datetime(2026, 11, 23, 0, 0, tzinfo=UTC))  # Mon 23 Nov, 05:30 IST
    assert days(fresh, logs((0, "done"), (1, "done"), (2, "done"))) == 3
    assert days(fresh, []) == 0


def test_backfilled_log_before_creation_counts() -> None:
    fresh = habit(created_at=datetime(2026, 11, 25, 6, 0, tzinfo=UTC))  # created today
    assert days(fresh, logs((0, "done"), (1, "done"))) == 2  # "did it yesterday too"


def test_no_logs_and_future_logs() -> None:
    assert days(habit(), []) == 0
    assert days(habit(), logs((-1, "done"), (0, "done"))) == 1  # tomorrow's log ignored


# --- week habits (Workout: Mon-Sat, target 3 per week) --------------------------------


def workout() -> HabitDTO:
    return habit(rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA", period="week", target=3)


def week_of(monday: date, *weekday_status: tuple[int, str]) -> list[tuple[int, str]]:
    """(weekday index, status) pairs for the week starting `monday`, as days-before-TODAY."""
    return [((TODAY - (monday + timedelta(days=wd))).days, s) for wd, s in weekday_status]


W0 = date(2026, 11, 23)  # current week (today is its Wednesday)
W1, W2, W3 = (W0 - timedelta(weeks=n) for n in (1, 2, 3))


def test_met_weeks_count_and_the_current_week_waits() -> None:
    entries = [
        *week_of(W3, (0, "done"), (2, "done"), (4, "done")),
        *week_of(W2, (0, "done"), (1, "done"), (5, "done")),
        *week_of(W1, (1, "done"), (3, "done"), (4, "done")),
        *week_of(W0, (0, "done")),  # 1 of 3 so far: neither counted nor broken
    ]
    assert days(workout(), logs(*entries)) == 3


def test_current_week_counts_once_met() -> None:
    entries = [*week_of(W1, (0, "done"), (1, "done"), (2, "done"))]
    entries += week_of(W0, (0, "done"), (1, "done"), (2, "done"))
    assert days(workout(), logs(*entries)) == 2


def test_a_short_past_week_breaks_the_streak() -> None:
    entries = [
        *week_of(W3, (0, "done"), (1, "done"), (2, "done")),
        *week_of(W2, (0, "done"), (1, "done")),  # 2 of 3: breaks here
        *week_of(W1, (0, "done"), (1, "done"), (2, "done")),
    ]
    assert days(workout(), logs(*entries)) == 1


def test_skips_can_excuse_a_week_without_counting_it() -> None:
    entries = [
        *week_of(W2, (0, "done"), (1, "done"), (2, "done")),
        *week_of(W1, (0, "done"), (1, "done"), (2, "skipped")),  # excused: neutral
    ]
    assert days(workout(), logs(*entries)) == 1


# --- IST boundaries with a frozen clock -----------------------------------------------


@time_machine.travel(datetime(2026, 11, 22, 18, 20, tzinfo=UTC), tick=False)  # Sun 23:50 IST
def test_2350_ist_sunday_log_counts_for_the_week_ending_that_sunday() -> None:
    today = t.today_local()
    assert today == date(2026, 11, 22)  # still Sunday in IST (UTC agrees here)
    entries = logs((6, "done"), (5, "done"), (0, "done"), base=today)  # Mon 16, Tue 17, Sun 22
    assert compute_streak(workout(), entries, today).current == 1


@time_machine.travel(datetime(2026, 11, 22, 19, 0, tzinfo=UTC), tick=False)  # Mon 00:30 IST
def test_0030_ist_monday_starts_a_new_week_and_a_new_day() -> None:
    today = t.today_local()
    assert today == date(2026, 11, 23)  # UTC still says Sunday 22nd
    # last week met; this new week has nothing yet -> streak 1, not broken
    entries = logs((7, "done"), (6, "done"), (1, "done"), base=today)
    assert compute_streak(workout(), entries, today).current == 1
    # daily habit: Sunday done, Monday (today) not logged yet -> still 2
    daily = logs((1, "done"), (2, "done"), base=today)
    assert compute_streak(habit(), daily, today).current == 2
