from datetime import UTC, date, datetime, timedelta

from sentry_ai.db.repositories import HabitRepo, ReminderRepo, TaskRepo

NOW = datetime(2026, 11, 22, 6, 30, tzinfo=UTC)  # Sun 22 Nov 12:00 IST (conftest default)
LATER = "2026-11-22T19:00:00+05:30"


async def new_reminder(call, **args):
    result = await call("create_reminder", **({"message": "Call mom", "fire_at": LATER} | args))
    assert result.ok, result.message
    return result.data["reminder"]["id"]


# --- create_reminder ----------------------------------------------------------------------


async def test_create_reminder_stores_row_and_job(call, session, user_id, scheduler):
    result = await call("create_reminder", message="Call mom", fire_at=LATER)
    assert result.ok and result.message.endswith("Sun 22 Nov 19:00: Call mom")
    reminder = await ReminderRepo(session, user_id).get(result.data["reminder"]["id"])
    assert reminder is not None
    assert (reminder.status, reminder.target_kind, reminder.job_id) == (
        "scheduled",
        "freeform",
        f"reminder:{reminder.id}",
    )
    assert scheduler.jobs == {reminder.id: datetime(2026, 11, 22, 13, 30, tzinfo=UTC)}


async def test_create_reminder_rejects_bad_input(call, session, user_id, scheduler):
    past = await call("create_reminder", message="x", fire_at="2026-11-22T11:59:00+05:30")
    assert past.error == "invalid_time"
    naive = await call("create_reminder", message="x", fire_at="2026-11-22T19:00:00")
    assert naive.error == "invalid_args"
    orphan = await call("create_reminder", message="x", fire_at=LATER, target_kind="task")
    assert orphan.error == "invalid_args"
    missing = await call(
        "create_reminder", message="x", fire_at=LATER, target_kind="task", target_id=999_999
    )
    assert missing.error == "not_found"
    assert await ReminderRepo(session, user_id).list_pending() == []
    assert scheduler.jobs == {}


async def test_create_reminder_one_minute_before_midnight_ist(call, session, user_id):
    task = await call("create_task", title="Pay rent")
    reminder_id = await new_reminder(
        call, fire_at="2026-11-22T23:59:00+05:30", target_kind="task",
        target_id=task.data["task"]["id"],
    )  # fmt: skip
    reminder = await ReminderRepo(session, user_id).get(reminder_id)
    assert reminder is not None and reminder.fire_at == datetime(2026, 11, 22, 18, 29, tzinfo=UTC)
    due_today = await ReminderRepo(session, user_id).list_pending(
        before=datetime(2026, 11, 22, 18, 30, tzinfo=UTC)  # end of the 22nd in IST
    )
    assert [r.id for r in due_today] == [reminder_id]


# --- snooze_reminder ----------------------------------------------------------------------


async def test_snooze_moves_fire_time_from_now(call, session, user_id, scheduler):
    reminder_id = await new_reminder(call)
    result = await call("snooze_reminder", reminder_id=reminder_id, minutes=30)
    assert result.ok
    reminder = await ReminderRepo(session, user_id).get(reminder_id)
    assert reminder is not None
    assert (reminder.status, reminder.fire_at) == ("snoozed", NOW + timedelta(minutes=30))
    assert scheduler.jobs[reminder_id] == reminder.fire_at


async def test_snooze_rejects_bad_input(call):
    reminder_id = await new_reminder(call)
    assert (await call("snooze_reminder", reminder_id=reminder_id, minutes=0)).error == (
        "invalid_args"
    )
    assert (await call("snooze_reminder", reminder_id=reminder_id, minutes=1441)).error == (
        "invalid_args"
    )
    assert (await call("snooze_reminder", reminder_id=999_999)).error == "not_found"
    await call("cancel_reminder", reminder_id=reminder_id)
    assert (await call("snooze_reminder", reminder_id=reminder_id)).error == "invalid_state"


async def test_snooze_across_midnight_ist(call, session, user_id):
    at = datetime(2026, 11, 22, 18, 20, tzinfo=UTC)  # 23:50 IST
    reminder_id = await new_reminder(call, fire_at="2026-11-22T23:55:00+05:30", now=at)
    result = await call("snooze_reminder", reminder_id=reminder_id, minutes=15, now=at)
    assert result.message == f"Snoozed reminder {reminder_id} to Mon 23 Nov 00:05."


# --- ack_reminder -------------------------------------------------------------------------


async def test_ack_done_completes_the_linked_task(call, session, user_id, scheduler):
    created = await call(
        "create_task", title="Submit SE", due_at="2026-11-27T17:00:00+05:30", remind_before_min=60
    )
    task_id, reminder_id = created.data["task"]["id"], created.data["reminder_id"]
    result = await call("ack_reminder", reminder_id=reminder_id, action="done")
    assert result.ok and result.message == (
        f"Reminder {reminder_id} done; task 'Submit SE' marked done."
    )
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None and task.status == "done"
    reminder = await ReminderRepo(session, user_id).get(reminder_id)
    assert reminder is not None and reminder.status == "acked"
    assert scheduler.jobs == {}


async def test_ack_rejects_bad_input(call):
    reminder_id = await new_reminder(call)
    assert (await call("ack_reminder", reminder_id=reminder_id, action="later")).error == (
        "invalid_args"
    )
    assert (await call("ack_reminder", reminder_id=999_999, action="done")).error == "not_found"
    assert (await call("ack_reminder", reminder_id=reminder_id, action="skip")).ok
    again = await call("ack_reminder", reminder_id=reminder_id, action="done")
    assert again.error == "invalid_state" and "already skipped" in again.message


async def test_ack_habit_after_midnight_ist_logs_the_new_day(call, session, user_id):
    habit = await call("create_habit", name="Read", rrule="FREQ=DAILY")
    habit_id = habit.data["habit"]["id"]
    reminder_id = await new_reminder(call, message="Read", target_kind="habit", target_id=habit_id)
    at = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # 00:30 IST on the 23rd
    result = await call("ack_reminder", reminder_id=reminder_id, action="skip", now=at)
    assert result.ok and "habit logged skipped for 2026-11-23" in result.message
    logs = await HabitRepo(session, user_id).logs_between(habit_id, date.min, date.max)
    assert [(log.local_date, log.status) for log in logs] == [(date(2026, 11, 23), "skipped")]


# --- cancel_reminder ----------------------------------------------------------------------


async def test_cancel_reminder_removes_the_job(call, session, user_id, scheduler):
    reminder_id = await new_reminder(call)
    result = await call("cancel_reminder", reminder_id=reminder_id)
    assert result.ok
    reminder = await ReminderRepo(session, user_id).get(reminder_id)
    assert reminder is not None and reminder.status == "cancelled"
    assert scheduler.jobs == {} and scheduler.cancelled == [reminder_id]
    assert (await call("cancel_reminder", reminder_id=reminder_id)).ok  # idempotent


async def test_cancel_reminder_rejects_bad_input(call):
    assert (await call("cancel_reminder")).error == "invalid_args"
    assert (await call("cancel_reminder", reminder_id=999_999)).error == "not_found"
    reminder_id = await new_reminder(call)
    await call("ack_reminder", reminder_id=reminder_id, action="done")
    assert (await call("cancel_reminder", reminder_id=reminder_id)).error == "invalid_state"


async def test_cancel_keeps_the_task_and_only_touches_mine(call, session, user_id):
    created = await call(
        "create_task", title="Pay rent", due_at="2026-11-23T00:30:00+05:30", remind_before_min=30
    )
    await call("cancel_reminder", reminder_id=created.data["reminder_id"])
    task = await TaskRepo(session, user_id).get(created.data["task"]["id"])
    assert task is not None and task.status == "todo"
    theirs = await ReminderRepo(session, "someone-else").list_pending()
    assert theirs == []
