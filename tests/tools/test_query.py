from datetime import UTC, date, datetime, time

import pytest
import sqlalchemy as sa

from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import (
    CalendarRepo,
    HabitRepo,
    PlanRepo,
    PreferenceRepo,
    RoutineRepo,
    TaskRepo,
)
from sentry_ai.db.schemas import (
    CalendarEventUpsert,
    HabitCreate,
    PlanBlockCreate,
    PlanBlockUpdate,
    RoutineCreate,
    TaskCreate,
)

MON = date(2026, 11, 23)
MON_0030 = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # 00:30 IST Monday; UTC is still Sunday
SUN_2300 = datetime(2026, 11, 22, 17, 30, tzinfo=UTC)


def ist(day: date, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day.isoformat()}T{hhmm}+05:30")


async def habit(session, user_id, name: str, **fields):
    created = await HabitRepo(session, user_id).create(
        HabitCreate(name=name, rrule="FREQ=DAILY", **fields)
    )
    # created long ago, so every day in these tests is a due day whatever the real clock says
    await session.execute(
        sa.update(Habit)
        .where(Habit.id == created.id)
        .values(created_at=datetime(2026, 11, 1, tzinfo=UTC))
    )
    return created.id


@pytest.fixture
async def college(session, user_id):
    await PreferenceRepo(session, user_id).set("no_tasks_before", {"time": "07:00"})
    await PreferenceRepo(session, user_id).set("sleep_time", {"time": "23:30"})
    await RoutineRepo(session, user_id).create(
        RoutineCreate(title="College", category="study", rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
                      start_time=time(9), duration_min=300, active_from=date(2026, 11, 1))
    )  # fmt: skip


# --- get_today_overview -------------------------------------------------------------------


async def test_overview_gathers_the_day(call, session, user_id, college):
    tasks = TaskRepo(session, user_id)
    await tasks.create(TaskCreate(title="SE", due_at=ist(MON, "17:00")))
    await tasks.create(TaskCreate(title="Overdue", due_at=ist(date(2026, 11, 20), "10:00")))
    await tasks.create(TaskCreate(title="Next week", due_at=ist(date(2026, 11, 30), "10:00")))
    await habit(session, user_id, "Read", preferred_time=time(21))
    await CalendarRepo(session, user_id).upsert_events(
        [CalendarEventUpsert(gcal_event_id="e1", calendar_id="primary", title="Talk",
                             start_at=ist(MON, "16:00"), end_at=ist(MON, "17:00"),
                             synced_at=SUN_2300)]
    )  # fmt: skip
    await call("create_reminder", message="Call mom", fire_at=ist(MON, "19:00").isoformat())
    result = await call("get_today_overview", date="2026-11-23")
    assert result.ok
    assert result.message == (
        "Mon 23 Nov: 1 routine block(s), 1 event(s), 2 task(s) due, 1 habit(s) due, "
        "1 reminder(s); no confirmed plan."
    )
    assert result.data["fixed_blocks"][0]["start_at"] == "2026-11-23T09:00+05:30"
    assert {t["title"] for t in result.data["tasks_due"]} == {"SE", "Overdue"}
    assert result.data["habits_due"][0] | {"habit_id": 0} == {
        "habit_id": 0, "name": "Read", "preferred_at": "2026-11-23T21:00+05:30", "logged": None,
    }  # fmt: skip
    assert result.data["plan"] is None


async def test_overview_rejects_bad_input(call):
    assert (await call("get_today_overview", date="tomorrow")).error == "invalid_args"
    assert (await call("get_today_overview", day="2026-11-23")).error == "invalid_args"


async def test_overview_after_midnight_ist_is_the_new_day(call, session, user_id):
    tasks = TaskRepo(session, user_id)
    await tasks.create(TaskCreate(title="Mon 23:50", due_at=ist(MON, "23:50")))
    await tasks.create(TaskCreate(title="Tue 00:30", due_at=ist(date(2026, 11, 24), "00:30")))
    result = await call("get_today_overview", now=MON_0030)
    assert result.data["date"] == "2026-11-23"
    assert [t["title"] for t in result.data["tasks_due"]] == ["Mon 23:50"]


async def test_overview_shows_the_confirmed_plan(call, session, user_id):
    plans = PlanRepo(session, user_id)
    plan = await plans.create_draft(
        MON, None, [PlanBlockCreate(start_at=ist(MON, "10:00"), end_at=ist(MON, "11:00"),
                                    kind="task", title="DSA")]
    )  # fmt: skip
    await plans.confirm(plan.id)
    result = await call("get_today_overview", date="2026-11-23")
    assert result.data["plan"]["plan_id"] == plan.id
    assert result.data["plan"]["blocks"][0]["title"] == "DSA"


# --- list_tasks ---------------------------------------------------------------------------


async def test_list_tasks_open_by_default(call, session, user_id):
    tasks = TaskRepo(session, user_id)
    await tasks.create(TaskCreate(title="low", priority=4))
    await tasks.create(TaskCreate(title="high", priority=1))
    done = await tasks.create(TaskCreate(title="done"))
    await tasks.set_status(done.id, "done", SUN_2300)
    assert [t["title"] for t in (await call("list_tasks")).data["tasks"]] == ["high", "low"]
    assert [t["title"] for t in (await call("list_tasks", status="done")).data["tasks"]] == ["done"]


async def test_list_tasks_rejects_bad_input(call):
    assert (await call("list_tasks", status="open")).error == "invalid_args"
    assert (await call("list_tasks", limit=0)).error == "invalid_args"
    assert (await call("list_tasks", due_before="2026-11-23T00:00:00")).error == "invalid_args"


async def test_list_tasks_due_before_local_midnight(call, session, user_id):
    tasks = TaskRepo(session, user_id)
    await tasks.create(TaskCreate(title="Sun 23:50", due_at=ist(date(2026, 11, 22), "23:50")))
    await tasks.create(TaskCreate(title="Mon 00:30", due_at=ist(MON, "00:30")))
    result = await call("list_tasks", due_before="2026-11-23T00:00:00+05:30")
    assert [t["title"] for t in result.data["tasks"]] == ["Sun 23:50"]


# --- get_habits_status --------------------------------------------------------------------


async def test_habits_status_today_with_streaks(call, session, user_id):
    read = await habit(session, user_id, "Read")
    gym = await habit(session, user_id, "Gym", period="week", target_per_period=4)
    repo = HabitRepo(session, user_id)
    for day in (date(2026, 11, 21), date(2026, 11, 22)):
        await repo.log(read, day, "done")
    await repo.log(gym, date(2026, 11, 20), "done")
    result = await call("get_habits_status")
    rows = {r["name"]: r for r in result.data["habits"]}
    assert (rows["Read"]["done"], rows["Read"]["target"], rows["Read"]["streak"]) == (1, 1, 2)
    assert (rows["Gym"]["done"], rows["Gym"]["target"]) == (0, None)  # weekly: no daily target
    week = {r["name"]: r for r in (await call("get_habits_status", period="week")).data["habits"]}
    assert (week["Gym"]["done"], week["Gym"]["target"]) == (1, 4)
    assert (week["Read"]["done"], week["Read"]["target"]) == (2, 7)


async def test_habits_status_rejects_bad_input(call):
    assert (await call("get_habits_status", period="month")).error == "invalid_args"
    assert (await call("get_habits_status")).message == "No habits tracked yet."


async def test_habits_week_resets_at_ist_midnight_on_monday(call, session, user_id):
    read = await habit(session, user_id, "Read")
    await HabitRepo(session, user_id).log(read, date(2026, 11, 22), "done")  # Sunday
    sunday = await call("get_habits_status", period="week", now=SUN_2300)
    monday = await call("get_habits_status", period="week", now=MON_0030)  # UTC still Sunday
    assert sunday.data["habits"][0]["done"] == 1
    assert monday.data["habits"][0]["done"] == 0


# --- get_free_slots -----------------------------------------------------------------------


async def test_free_slots_around_fixed_blocks(call, college):
    result = await call("get_free_slots", date="2026-11-23")
    assert result.message == "2 free slot(s) on Mon 23 Nov: 07:00-08:50, 14:10-23:30."
    assert result.data["total_minutes"] == 110 + 560


async def test_free_slots_rejects_bad_input(call):
    assert (await call("get_free_slots", date="2026-11-21")).error == "invalid_args"
    assert (await call("get_free_slots", min_duration_min=0)).error == "invalid_args"


async def test_free_slots_late_tonight_and_around_a_confirmed_plan(call, session, user_id, college):
    tonight = await call("get_free_slots", now=SUN_2300, min_duration_min=15)
    assert tonight.message == "1 free slot(s) on Sun 22 Nov: 23:00-23:30."
    plans = PlanRepo(session, user_id)
    plan = await plans.create_draft(
        MON, None, [PlanBlockCreate(start_at=ist(MON, "15:00"), end_at=ist(MON, "16:00"),
                                    kind="task", title="DSA")]
    )  # fmt: skip
    await plans.confirm(plan.id)
    monday = await call("get_free_slots", date="2026-11-23")
    assert "14:10-14:50, 16:10-23:30" in monday.message


# --- get_stats ----------------------------------------------------------------------------


async def test_stats_for_the_last_week(call, session, user_id):
    tasks = TaskRepo(session, user_id)
    for title, status in (("a", "done"), ("b", "done"), ("c", "todo"), ("d", "cancelled")):
        task = await tasks.create(TaskCreate(title=title, due_at=ist(date(2026, 11, 20), "10:00")))
        if status != "todo":
            await tasks.set_status(task.id, status, SUN_2300)
    rolled = (await tasks.query(project=None, status=["todo"]))[0]
    await tasks.increment_rollover(rolled.id)
    read = await habit(session, user_id, "Read")
    for day in range(16, 21):  # 5 of the 7 days
        await HabitRepo(session, user_id).log(read, date(2026, 11, day), "done")
    result = await call("get_stats")
    data = result.data
    assert (data["from"], data["to"]) == ("2026-11-16", "2026-11-22")
    assert (data["tasks_due"], data["tasks_done"], data["task_completion_pct"]) == (3, 2, 67)
    assert (data["habit_pct"], data["rollovers"]) == (71, 1)
    assert result.message.startswith("16 Nov-22 Nov: tasks 2/3 done, habits 71%")


async def test_stats_rejects_bad_input(call):
    assert (await call("get_stats", range="year")).error == "invalid_args"
    empty = await call("get_stats", range="month")
    assert empty.ok and empty.data["task_completion_pct"] is None


async def test_stats_window_and_plan_adherence_follow_ist_days(call, session, user_id):
    tasks = TaskRepo(session, user_id)
    await tasks.create(TaskCreate(title="in", due_at=ist(date(2026, 11, 22), "23:50")))
    await tasks.create(TaskCreate(title="out", due_at=ist(MON, "00:30")))
    plans = PlanRepo(session, user_id)
    sun = date(2026, 11, 22)
    blocks = [
        PlanBlockCreate(start_at=ist(sun, a), end_at=ist(sun, b), kind="task", title=a)
        for a, b in (("08:00", "09:00"), ("10:00", "11:00"))
    ]
    plan = await plans.create_draft(sun, None, blocks)
    await plans.confirm(plan.id)
    await plans.update_block(plan.blocks[0].id, PlanBlockUpdate(outcome="done"))
    data = (await call("get_stats")).data  # now = Sun 12:00 IST
    assert data["tasks_due"] == 1
    assert (data["plan_blocks"], data["plan_blocks_done"], data["plan_adherence_pct"]) == (2, 1, 50)
