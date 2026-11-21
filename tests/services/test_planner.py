from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from sentry_ai.services.intervals import Interval, daily, intersect, merge, subtract
from sentry_ai.services.planner import (
    Busy,
    Flexible,
    Prefs,
    TaskItem,
    check_slot,
    frame,
    plan_day,
)

IST = ZoneInfo("Asia/Kolkata")
MON = date(2026, 11, 23)
PROFILE = Prefs(
    wake=time(6, 30),
    no_tasks_before=time(7),
    sleep=time(23, 30),
    quiet=(time(23, 30), time(6, 30)),
    deep_work=(time(21), time(23)),
    max_tasks=5,
)


def at(hhmm: str, day: date = MON) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def span(a: str, b: str, day: date = MON) -> Interval:
    return Interval(at(a, day), at(b, day))


def busy(title: str, a: str, b: str) -> Busy:
    return Busy(at(a), at(b), title, "routine", None)


def task(
    id: int, minutes: int, *, priority: int = 2, due: str | None = None, title: str = ""
) -> TaskItem:
    return TaskItem(id, title or f"task {id}", priority, at(due) if due else None, minutes)


def local(slots: object) -> list[tuple[str, str, str]]:
    return [
        (
            p.title,
            p.start.astimezone(IST).strftime("%H:%M"),
            p.end.astimezone(IST).strftime("%H:%M"),
        )
        for p in slots  # type: ignore[attr-defined]
    ]


COLLEGE, GYM = busy("College", "09:00", "14:00"), busy("Gym", "18:00", "19:30")
LUNCH = Flexible("routine", 3, "Lunch", at("13:30"), 45)
DSA = Flexible("habit", 1, "DSA practice", at("21:00"), 30)
READING = Flexible("habit", 2, "Reading", at("23:00"), 30)


# --- interval maths -----------------------------------------------------------------------


def test_subtract_merge_intersect() -> None:
    day = [span("07:00", "23:00")]
    assert subtract(day, span("09:00", "14:00")) == [span("07:00", "09:00"), span("14:00", "23:00")]
    assert subtract(day, span("05:00", "08:00")) == [span("08:00", "23:00")]
    assert subtract(day, span("05:00", "06:00")) == day
    assert merge([span("10:00", "11:00"), span("09:00", "10:00"), span("12:00", "13:00")]) == [
        span("09:00", "11:00"),
        span("12:00", "13:00"),
    ]
    assert intersect(day, [span("06:00", "08:00"), span("22:00", "23:30")]) == [
        span("07:00", "08:00"),
        span("22:00", "23:00"),
    ]


def test_daily_window_wrapping_midnight() -> None:
    windows = daily(MON, (time(23, 30), time(6, 30)))
    assert span("23:30", "06:30", MON - timedelta(days=1)).start == windows[0].start
    assert windows[1] == Interval(at("23:30"), at("06:30", MON + timedelta(days=1)))
    assert all(w.minutes == 7 * 60 for w in windows)


# --- preferences and the day frame -------------------------------------------------------


def test_prefs_from_stored_values_and_defaults() -> None:
    prefs = Prefs.from_preferences(
        {
            "wake_time": {"time": "06:30"},
            "quiet_hours": {"start": "23:30", "end": "06:30"},
            "max_tasks_per_day": {"count": 5},
            "default_reminder_min": {"minutes": 15},
        }
    )
    assert (prefs.wake, prefs.quiet, prefs.max_tasks, prefs.reminder_min) == (
        time(6, 30),
        (time(23, 30), time(6, 30)),
        5,
        15,
    )
    assert Prefs.from_preferences({}) == Prefs()
    assert frame(MON, Prefs()) == [span("07:00", "23:00")]  # defaults


def test_frame_runs_past_midnight_and_stops_at_quiet_hours() -> None:
    prefs = Prefs(no_tasks_before=time(8), sleep=time(1), quiet=(time(0, 30), time(6, 30)))
    assert frame(MON, prefs) == [Interval(at("08:00"), at("00:30", MON + timedelta(days=1)))]


def test_today_starts_at_the_next_quarter_hour() -> None:
    assert frame(MON, PROFILE, now=at("10:07"))[0].start == at("10:15")
    assert frame(MON, PROFILE, now=at("10:15"))[0].start == at("10:15")
    assert frame(MON, PROFILE, now=at("23:40")) == []


# --- placing ------------------------------------------------------------------------------


def test_a_profile_day() -> None:
    plan = plan_day(
        MON,
        PROFILE,
        busy=[COLLEGE, GYM],
        flexible=[LUNCH, DSA, READING],
        tasks=[
            task(1, 60, priority=1, due="17:00", title="SE assignment"),
            task(2, 90, title="DSA revision"),
            task(3, 30, priority=3, title="Groceries"),
        ],
    )
    assert local(plan.placed) == [
        ("SE assignment", "07:00", "08:00"),  # due today: earliest slot before 17:00
        ("Groceries", "08:10", "08:40"),
        ("Lunch", "14:10", "14:55"),  # flexible: moved past College + buffer
        ("DSA revision", "15:05", "16:35"),  # 90 min does not fit the deep-work gap left
        ("DSA practice", "21:00", "21:30"),
        ("Reading", "23:00", "23:30"),
    ]
    assert (plan.unplaced, plan.deferred, plan.conflicts) == ((), (), ())
    assert plan.overcommitted_by_min == 0


def test_long_tasks_prefer_the_deep_work_window() -> None:
    plan = plan_day(MON, PROFILE, busy=[COLLEGE], tasks=[task(1, 60), task(2, 30)])
    assert local(plan.placed) == [("task 2", "07:00", "07:30"), ("task 1", "21:00", "22:00")]


def test_overcommitment_is_reported_not_squeezed() -> None:
    prefs = Prefs(no_tasks_before=time(7), sleep=time(9))
    plan = plan_day(MON, prefs, tasks=[task(1, 90), task(2, 60), task(3, 30)])
    assert local(plan.placed) == [("task 1", "07:00", "08:30")]
    assert [(u.ref_id, u.reason) for u in plan.unplaced] == [
        (2, "no free 60-min slot"),
        (3, "no free 30-min slot"),
    ]
    assert plan.overcommitted_by_min == 90
    assert plan.free_minutes == 20  # 08:40-09:00 is left, too short for either


def test_max_tasks_per_day_defers_the_rest_in_priority_order() -> None:
    prefs = Prefs(max_tasks=2)
    plan = plan_day(
        MON, prefs, tasks=[task(1, 30, priority=3), task(2, 30), task(3, 30, priority=1)]
    )
    assert [p.ref_id for p in plan.placed] == [3, 2]
    assert [(d.ref_id, d.reason) for d in plan.deferred] == [(1, "over max_tasks_per_day")]
    assert plan.overcommitted_by_min == 0  # deferring on purpose is not overcommitment


def test_llm_order_and_focus_come_first() -> None:
    tasks = [task(1, 30, priority=1), task(2, 30, title="ML notes"), task(3, 30, priority=4)]
    by_order = plan_day(MON, Prefs(max_tasks=1), tasks=tasks, task_order=[3])
    assert [p.ref_id for p in by_order.placed] == [3]
    by_focus = plan_day(MON, Prefs(max_tasks=1), tasks=tasks, focus="ml")
    assert [p.ref_id for p in by_focus.placed] == [2]


def test_overdue_beats_priority() -> None:
    tasks = [task(1, 30, priority=1), task(2, 30, priority=4, due="06:00")]
    plan = plan_day(MON, Prefs(max_tasks=1), tasks=tasks)
    assert [p.ref_id for p in plan.placed] == [2]


def test_available_windows_limit_placement() -> None:
    plan = plan_day(MON, PROFILE, tasks=[task(1, 30)], windows=[span("15:00", "17:00")])
    assert local(plan.placed) == [("task 1", "15:00", "15:30")]


def test_flexible_items_never_move_more_than_two_hours() -> None:
    plan = plan_day(MON, PROFILE, busy=[busy("Exam", "11:00", "17:00")], flexible=[LUNCH])
    assert plan.placed == ()
    assert plan.unplaced[0].reason == "no free 45 min within 2 h of 13:30"


def test_clashing_fixed_blocks_are_reported() -> None:
    lunch = busy("Lunch", "13:30", "14:15")
    plan = plan_day(MON, PROFILE, busy=[COLLEGE, lunch])
    assert plan.conflicts == ("'College' (09:00-14:00) overlaps 'Lunch' (13:30-14:15)",)


# --- check_slot (move_block) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("slot", "reason"),
    [
        (span("15:00", "16:00"), None),
        (span("06:30", "07:30"), "outside your planning hours"),
        (span("23:00", "23:45"), "outside your planning hours"),
        (span("13:00", "14:30"), "overlap 'College' (09:00-14:00)"),
    ],
)
def test_check_slot(slot: Interval, reason: str | None) -> None:
    found = check_slot(MON, PROFILE, slot, [("College", Interval(COLLEGE.start, COLLEGE.end))])
    assert found is None if reason is None else (found is not None and reason in found)
