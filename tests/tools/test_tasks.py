from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import ReminderRepo, TaskRepo
from sentry_ai.db.schemas import TaskCreate

NOW = datetime(2026, 11, 22, 6, 30, tzinfo=UTC)  # Sun 22 Nov 12:00 IST (conftest default)


async def new_task(call, **args):
    result = await call("create_task", **args)
    assert result.ok, result.message
    return result.data["task"]["id"]


# --- create_task --------------------------------------------------------------------------


async def test_create_task_with_reminder(call, session: AsyncSession, user_id: str, scheduler):
    result = await call(
        "create_task",
        title="Submit SE assignment",
        due_at="2026-11-27T17:00:00+05:30",
        remind_before_min=60,
        priority=1,
    )
    assert result.ok
    assert result.message == (
        "Created task {id} 'Submit SE assignment', due Fri 27 Nov 17:00; "
        "reminder at Fri 27 Nov 16:00.".format(id=result.data["task"]["id"])
    )
    task = await TaskRepo(session, user_id).get(result.data["task"]["id"])
    assert task is not None and task.priority == 1
    reminder = await ReminderRepo(session, user_id).get(result.data["reminder_id"])
    assert reminder is not None
    assert (reminder.target_kind, reminder.target_id) == ("task", task.id)
    assert reminder.fire_at == datetime(2026, 11, 27, 10, 30, tzinfo=UTC)
    assert reminder.job_id == f"reminder:{reminder.id}"
    assert scheduler.jobs == {reminder.id: reminder.fire_at}


async def test_create_task_rejects_bad_input(call, session: AsyncSession, user_id: str):
    naive = await call("create_task", title="x", due_at="2026-11-27T17:00:00")
    assert (naive.ok, naive.error) == (False, "invalid_args")
    assert "due_at" in naive.message
    assert (await call("create_task", title="x", priority=5)).error == "invalid_args"
    assert (await call("create_task", title="   ")).error == "invalid_args"
    no_due = await call("create_task", title="x", remind_before_min=10)
    assert no_due.error == "invalid_args"
    assert await TaskRepo(session, user_id).query() == []


async def test_create_task_late_evening_ist_stays_on_that_local_day(call, session, user_id):
    result = await call("create_task", title="Night task", due_at="2026-11-22T23:50:00+05:30")
    task = await TaskRepo(session, user_id).get(result.data["task"]["id"])
    assert task is not None
    assert task.due_at == datetime(2026, 11, 22, 18, 20, tzinfo=UTC)
    assert result.data["task"]["due_at"] == "2026-11-22T23:50+05:30"
    assert "due Sun 22 Nov 23:50" in result.message


async def test_create_task_skips_a_reminder_that_would_be_in_the_past(call, session, user_id):
    due = (NOW + timedelta(minutes=30)).isoformat()
    result = await call("create_task", title="Soon", due_at=due, remind_before_min=60)
    assert result.ok
    assert "already passed" in result.message
    assert "reminder_id" not in result.data
    assert await ReminderRepo(session, user_id).list_pending() == []


# --- update_task --------------------------------------------------------------------------


async def test_update_task_changes_fields(call, session, user_id):
    task_id = await new_task(call, title="Read paper", priority=3)
    result = await call("update_task", task_id=task_id, priority=1, project="ML")
    assert result.ok
    assert result.message == f"Updated task {task_id} 'Read paper': priority, project."
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None and (task.priority, task.project) == (1, "ML")


async def test_update_task_rejects_bad_input(call):
    task_id = await new_task(call, title="Read paper")
    assert (await call("update_task", task_id=task_id)).error == "invalid_args"
    assert (await call("update_task", task_id=task_id, title=None)).error == "invalid_args"
    assert (await call("update_task", task_id=999_999, priority=1)).error == "not_found"


async def test_moving_due_at_moves_the_reminder(call, session, user_id, scheduler):
    created = await call(
        "create_task", title="SE", due_at="2026-11-27T17:00:00+05:30", remind_before_min=60
    )
    task_id, reminder_id = created.data["task"]["id"], created.data["reminder_id"]
    # 00:30 IST on the 28th is 19:00 UTC on the 27th
    result = await call("update_task", task_id=task_id, due_at="2026-11-28T00:30:00+05:30")
    assert result.ok
    reminder = await ReminderRepo(session, user_id).get(reminder_id)
    assert reminder is not None
    assert reminder.fire_at == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # still 60 min before
    assert scheduler.jobs[reminder_id] == reminder.fire_at


async def test_clearing_due_at_cancels_the_reminder(call, session, user_id, scheduler):
    created = await call(
        "create_task", title="SE", due_at="2026-11-27T17:00:00+05:30", remind_before_min=60
    )
    await call("update_task", task_id=created.data["task"]["id"], due_at=None)
    reminder = await ReminderRepo(session, user_id).get(created.data["reminder_id"])
    assert reminder is not None and reminder.status == "cancelled"
    assert scheduler.jobs == {}


# --- complete_task / skip_task ------------------------------------------------------------


async def test_complete_task_sets_done_and_cancels_reminders(call, session, user_id, scheduler):
    created = await call(
        "create_task", title="SE", due_at="2026-11-27T17:00:00+05:30", remind_before_min=60
    )
    task_id = created.data["task"]["id"]
    result = await call("complete_task", task_id=task_id)
    assert result.ok and result.data["reminders_cancelled"] == 1
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None and (task.status, task.completed_at) == ("done", NOW)
    assert scheduler.jobs == {}
    again = await call("complete_task", task_id=task_id)
    assert again.ok and "already done" in again.message


async def test_complete_task_rejects_bad_input(call):
    assert (await call("complete_task", task_id="abc")).error == "invalid_args"
    assert (await call("complete_task")).error == "invalid_args"
    missing = await call("complete_task", task_id=999_999)
    assert missing.error == "not_found" and "find_task" in missing.message


async def test_complete_task_just_after_midnight_ist(call, session, user_id):
    task_id = await new_task(call, title="Late")
    at = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # 00:30 IST on the 23rd
    result = await call("complete_task", task_id=task_id, now=at)
    assert result.ok
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None and task.completed_at == at


async def test_skip_task_records_reason_and_cancels_reminders(call, session, user_id, scheduler):
    created = await call(
        "create_task",
        title="Gym bag",
        notes="blue one",
        due_at="2026-11-27T17:00:00+05:30",
        remind_before_min=15,
    )
    task_id = created.data["task"]["id"]
    result = await call("skip_task", task_id=task_id, reason="gym closed")
    assert result.ok
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None
    assert (task.status, task.notes) == ("skipped", "blue one\nSkipped: gym closed")
    assert scheduler.jobs == {}


async def test_skip_task_rejects_bad_input_and_closed_tasks(call):
    task_id = await new_task(call, title="Done already")
    await call("complete_task", task_id=task_id)
    closed = await call("skip_task", task_id=task_id)
    assert (closed.ok, closed.error) == (False, "invalid_state")
    assert (await call("skip_task", task_id=task_id, reason="x" * 501)).error == "invalid_args"
    assert (await call("skip_task", task_id=999_999)).error == "not_found"


async def test_skip_task_late_night_ist_keeps_notes_unchanged_without_reason(
    call, session, user_id
):
    task_id = await new_task(call, title="Night walk", notes="n")
    at = datetime(2026, 11, 22, 18, 20, tzinfo=UTC)  # 23:50 IST
    assert (await call("skip_task", task_id=task_id, now=at)).ok
    task = await TaskRepo(session, user_id).get(task_id)
    assert task is not None and (task.status, task.notes, task.completed_at) == (
        "skipped",
        "n",
        None,
    )


# --- find_task ----------------------------------------------------------------------------


async def test_find_task_matches_loose_descriptions(call):
    se = await new_task(call, title="Submit SE assignment")
    await new_task(call, title="Buy groceries")
    result = await call("find_task", query="SE thing")
    assert result.ok
    assert result.data["matches"][0]["id"] == se
    assert result.message == f"Best match: task {se} 'Submit SE assignment'."


async def test_find_task_rejects_bad_input(call):
    assert (await call("find_task", query="")).error == "invalid_args"
    assert (await call("find_task", query="x" * 201)).error == "invalid_args"
    assert (await call("find_task", query="se", limit=3)).error == "invalid_args"


async def test_find_task_skips_closed_and_other_users(call, session: AsyncSession):
    done = await new_task(call, title="Submit SE assignment")
    await call("complete_task", task_id=done)
    await TaskRepo(session, "someone-else").create(TaskCreate(title="Submit SE report"))
    open_only = await call("find_task", query="SE assignment")
    assert open_only.data["matches"] == [] and "No task matches" in open_only.message
    closed_too = await call("find_task", query="SE assignment", include_closed=True)
    assert [m["id"] for m in closed_too.data["matches"]] == [done]
