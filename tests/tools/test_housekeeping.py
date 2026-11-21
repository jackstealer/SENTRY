from datetime import UTC, date, datetime, time

from sentry_ai.db.repositories import HabitRepo, ReminderRepo, RoutineRepo, TaskRepo
from sentry_ai.db.schemas import HabitCreate, RoutineCreate, TaskCreate

LATER = "2026-11-22T19:00:00+05:30"


async def done_task(session, user_id, title: str, completed_at: datetime) -> int:
    tasks = TaskRepo(session, user_id)
    task = await tasks.create(TaskCreate(title=title))
    await tasks.set_status(task.id, "done", completed_at)
    return task.id


# --- archive_tasks ------------------------------------------------------------------------


async def test_archive_tasks_asks_first_then_archives(call, session, user_id):
    await done_task(session, user_id, "old", datetime(2026, 10, 5, tzinfo=UTC))
    await done_task(session, user_id, "recent", datetime(2026, 11, 20, tzinfo=UTC))
    asked = await call("archive_tasks", completed_before="2026-11-01")
    assert (asked.ok, asked.error) == (False, "confirmation_required")
    assert asked.data["pending"]["summary"] == (
        "This will archive 1 completed task(s) finished before 2026-11-01."
    )
    assert len(await TaskRepo(session, user_id).query(status=["done"])) == 2  # untouched
    result = await call("archive_tasks", completed_before="2026-11-01", confirmed=True)
    assert result.ok and result.data["archived"] == 1
    assert [t.title for t in await TaskRepo(session, user_id).query(status=["done"])] == ["recent"]


async def test_archive_tasks_rejects_bad_input_and_nothing_to_do(call):
    assert (await call("archive_tasks")).error == "invalid_args"
    assert (await call("archive_tasks", completed_before="last month")).error == "invalid_args"
    nothing = await call("archive_tasks", completed_before="2026-11-01")
    assert nothing.error == "nothing_to_do"  # no confirmation prompt for nothing


async def test_archive_cutoff_is_local_midnight(call, session, user_id):
    # 23:50 IST on 31 Oct is 18:20 UTC: before the 1 Nov cutoff; 00:30 IST on 1 Nov is not
    await done_task(session, user_id, "31 Oct 23:50", datetime(2026, 10, 31, 18, 20, tzinfo=UTC))
    await done_task(session, user_id, "1 Nov 00:30", datetime(2026, 10, 31, 19, 0, tzinfo=UTC))
    result = await call("archive_tasks", completed_before="2026-11-01", confirmed=True)
    assert result.data["archived"] == 1
    left = await TaskRepo(session, user_id).query(status=["done"])
    assert [t.title for t in left] == ["1 Nov 00:30"]


# --- delete_habit -------------------------------------------------------------------------


async def new_habit(session, user_id) -> int:
    habits = HabitRepo(session, user_id)
    habit = await habits.create(
        HabitCreate(name="Read", rrule="FREQ=DAILY", preferred_time=time(21))
    )
    await habits.log(habit.id, date(2026, 11, 21), "done")
    return habit.id


async def test_delete_habit_asks_first_then_deletes_with_logs(call, session, user_id, scheduler):
    habit_id = await new_habit(session, user_id)
    reminder = await call(
        "create_reminder", message="Read", fire_at=LATER, target_kind="habit", target_id=habit_id
    )
    asked = await call("delete_habit", habit_id=habit_id)
    assert asked.error == "confirmation_required"
    assert (
        asked.data["pending"]["summary"]
        == "This will permanently delete habit 'Read' and its 1 log(s)."
    )
    assert await HabitRepo(session, user_id).get(habit_id) is not None
    result = await call("delete_habit", habit_id=habit_id, confirmed=True)
    assert result.ok and result.data["reminders_cancelled"] == 1
    assert await HabitRepo(session, user_id).get(habit_id) is None
    assert await HabitRepo(session, user_id).logs_between(habit_id, date.min, date.max) == []
    stored = await ReminderRepo(session, user_id).get(reminder.data["reminder"]["id"])
    assert stored is not None and stored.status == "cancelled" and scheduler.jobs == {}


async def test_delete_habit_rejects_bad_input(call):
    assert (await call("delete_habit", habit_id="Read")).error == "invalid_args"
    assert (await call("delete_habit", habit_id=999_999)).error == "not_found"
    assert (await call("delete_habit", habit_id=999_999, confirmed=True)).error == "not_found"


async def test_delete_habit_only_touches_mine(call, session, user_id):
    theirs = await new_habit(session, "someone-else")
    assert (await call("delete_habit", habit_id=theirs, confirmed=True)).error == "not_found"
    assert await HabitRepo(session, "someone-else").get(theirs) is not None


# --- delete_routine -----------------------------------------------------------------------


async def new_routine(session, user_id) -> int:
    routine = await RoutineRepo(session, user_id).create(
        RoutineCreate(title="Gym", category="fitness", rrule="FREQ=DAILY", start_time=time(18),
                      duration_min=90)
    )  # fmt: skip
    return routine.id


async def test_delete_routine_asks_first_then_deletes(call, session, user_id):
    routine_id = await new_routine(session, user_id)
    asked = await call("delete_routine", routine_id=routine_id)
    assert asked.error == "confirmation_required"
    assert asked.data["pending"]["summary"] == (
        "This will permanently delete routine 'Gym' (FREQ=DAILY at 18:00)."
    )
    assert await RoutineRepo(session, user_id).get(routine_id) is not None
    result = await call("delete_routine", routine_id=routine_id, confirmed=True)
    assert result.ok and await RoutineRepo(session, user_id).get(routine_id) is None


async def test_delete_routine_rejects_bad_input(call):
    assert (await call("delete_routine")).error == "invalid_args"
    assert (await call("delete_routine", routine_id=999_999)).error == "not_found"


async def test_delete_routine_cancels_its_reminders(call, session, user_id, scheduler):
    routine_id = await new_routine(session, user_id)
    reminder = await call(
        "create_reminder", message="Gym", fire_at=LATER, target_kind="routine", target_id=routine_id
    )
    result = await call("delete_routine", routine_id=routine_id, confirmed=True)
    assert result.data["reminders_cancelled"] == 1
    stored = await ReminderRepo(session, user_id).get(reminder.data["reminder"]["id"])
    assert stored is not None and stored.status == "cancelled"
