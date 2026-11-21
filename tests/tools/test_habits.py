from datetime import UTC, date, datetime, time

from sentry_ai.db.repositories import HabitRepo
from sentry_ai.services.routines import habits_due

AFTER_MIDNIGHT = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # 00:30 IST, Mon 23 Nov


async def new_habit(call, **args):
    result = await call("create_habit", **({"name": "DSA practice", "rrule": "FREQ=DAILY"} | args))
    assert result.ok, result.message
    return result.data["habit"]["id"]


# --- create_habit -------------------------------------------------------------------------


async def test_create_habit_stores_it(call, session, user_id):
    result = await call(
        "create_habit", name="DSA practice", rrule="freq=daily", preferred_time="21:00"
    )
    assert result.ok
    assert result.data["habit"]["preferred_time"] == "21:00"
    habit = await HabitRepo(session, user_id).get_by_name("dsa practice")
    assert habit is not None
    assert (habit.rrule, habit.preferred_time, habit.period) == ("FREQ=DAILY", time(21), "day")


async def test_create_habit_rejects_bad_input_and_duplicates(call):
    assert (await call("create_habit", name="x", rrule="FREQ=SOMETIMES")).error == "invalid_args"
    assert (await call("create_habit", name="x", rrule="FREQ=DAILY", period="month")).error == (
        "invalid_args"
    )
    week = await call(
        "create_habit", name="x", rrule="FREQ=DAILY", period="week", target_per_period=8
    )
    assert week.error == "invalid_args"
    bad_time = await call("create_habit", name="x", rrule="FREQ=DAILY", preferred_time="25:00")
    assert bad_time.error == "invalid_args"
    await new_habit(call)
    assert (await call("create_habit", name="DSA PRACTICE", rrule="FREQ=DAILY")).error == (
        "duplicate"
    )


async def test_habit_created_after_midnight_ist_is_due_that_local_day(call, session, user_id):
    result = await call("create_habit", name="Stretch", rrule="FREQ=DAILY", now=AFTER_MIDNIGHT)
    habit = await HabitRepo(session, user_id).get(result.data["habit"]["id"])
    assert habit is not None
    # created 19:00 UTC on the 22nd, which is the 23rd in IST: that is its first due day
    assert [h.name for h in habits_due([habit], date(2026, 11, 23))] == ["Stretch"]


# --- log_habit ----------------------------------------------------------------------------


async def test_log_habit_by_name_returns_the_streak(call, session, user_id):
    habit_id = await new_habit(call)
    await call("log_habit", habit_id=habit_id, status="done", date="2026-11-21")
    result = await call("log_habit", habit_name="dsa practice", status="done")
    assert result.ok
    assert (result.data["local_date"], result.data["streak"]) == ("2026-11-22", 2)
    assert "Streak: 2 day(s)" in result.message
    logs = await HabitRepo(session, user_id).logs_between(habit_id, date.min, date.max)
    assert [(log.local_date, log.status) for log in logs] == [
        (date(2026, 11, 21), "done"),
        (date(2026, 11, 22), "done"),
    ]


async def test_log_habit_rejects_bad_input(call):
    habit_id = await new_habit(call)
    both = await call("log_habit", habit_id=habit_id, habit_name="DSA practice", status="done")
    assert both.error == "invalid_args"
    assert (await call("log_habit", habit_id=habit_id, status="missed")).error == "invalid_args"
    future = await call("log_habit", habit_id=habit_id, status="done", date="2026-11-23")
    assert future.error == "invalid_args" and "future" in future.message
    unknown = await call("log_habit", habit_name="Juggling", status="done")
    assert unknown.error == "not_found" and "'DSA practice'" in unknown.message


async def test_log_habit_after_midnight_ist_logs_the_new_local_day(call, session, user_id):
    habit_id = await new_habit(call)
    result = await call("log_habit", habit_id=habit_id, status="skipped", now=AFTER_MIDNIGHT)
    assert result.data["local_date"] == "2026-11-23"  # UTC date is still the 22nd
    logs = await HabitRepo(session, user_id).logs_between(habit_id, date.min, date.max)
    assert [(log.local_date, log.status) for log in logs] == [(date(2026, 11, 23), "skipped")]


# --- update_habit -------------------------------------------------------------------------


async def test_update_habit_changes_only_given_fields(call, session, user_id):
    habit_id = await new_habit(call, preferred_time="21:00")
    result = await call("update_habit", habit_id=habit_id, target_per_period=2, name="DSA x2")
    assert result.ok and result.message.endswith("name, target_per_period.")
    habit = await HabitRepo(session, user_id).get(habit_id)
    assert habit is not None
    assert (habit.name, habit.target_per_period, habit.preferred_time) == ("DSA x2", 2, time(21))


async def test_update_habit_rejects_bad_input(call):
    habit_id = await new_habit(call)
    assert (await call("update_habit", habit_id=habit_id)).error == "invalid_args"
    assert (await call("update_habit", habit_id=habit_id, name=None)).error == "invalid_args"
    assert (await call("update_habit", habit_id=habit_id, rrule="nope")).error == "invalid_args"
    assert (await call("update_habit", habit_id=999_999, name="x")).error == "not_found"


async def test_update_habit_midnight_time_and_clearing_it(call, session, user_id):
    habit_id = await new_habit(call, preferred_time="21:00")
    await call("update_habit", habit_id=habit_id, preferred_time="00:00")
    habit = await HabitRepo(session, user_id).get(habit_id)
    assert habit is not None and habit.preferred_time == time(0, 0)
    # local midnight on the 23rd is 18:30 UTC on the 22nd
    due = habits_due([habit], date(2026, 11, 23))
    assert due[0].preferred_at == datetime(2026, 11, 22, 18, 30, tzinfo=UTC)
    await call("update_habit", habit_id=habit_id, preferred_time=None)
    habit = await HabitRepo(session, user_id).get(habit_id)
    assert habit is not None and habit.preferred_time is None
