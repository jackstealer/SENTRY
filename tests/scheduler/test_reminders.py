from datetime import timedelta

from sentry_ai.db.repositories import ReminderRepo, TaskRepo
from sentry_ai.db.schemas import ReminderCreate, ReminderDTO, ReminderUpdate, TaskCreate
from sentry_ai.scheduler.reminder_jobs import fire_nudge, fire_reminder, reconcile, reminder_text
from sentry_ai.scheduler.reminders import NUDGE_JOB, REMINDER_JOB, ApsReminderScheduler
from sentry_ai.services.reminders import ReminderService

from .conftest import NOW

IN_AN_HOUR = NOW + timedelta(hours=1)


def jobs(scheduler):  # type: ignore[no-untyped-def]
    return {job.id: job for job in scheduler.get_jobs()}


def buttons(reminder_id: int) -> list[str]:
    return [f"reminder:{reminder_id}:{a}" for a in ("done", "snooze15", "skip")]


# --- ApsReminderScheduler -----------------------------------------------------------------


async def test_schedule_adds_or_replaces_one_date_job(env, session, user_id) -> None:
    reminder = await ReminderRepo(session, user_id).create(
        ReminderCreate(message="Call mom", fire_at=IN_AN_HOUR)
    )
    aps = ApsReminderScheduler(env.scheduler)
    assert await aps.schedule(reminder) == f"reminder:{reminder.id}"
    aps.schedule_nudge(reminder.id, IN_AN_HOUR + timedelta(minutes=15))
    later = reminder.model_copy(update={"fire_at": IN_AN_HOUR + timedelta(minutes=30)})
    await aps.schedule(later)  # snoozed or moved
    found = jobs(env.scheduler)
    assert list(found) == [f"reminder:{reminder.id}"]  # replaced, and the stale nudge is gone
    job = found[f"reminder:{reminder.id}"]
    assert (job.func_ref, job.args, job.misfire_grace_time) == (REMINDER_JOB, (reminder.id,), 300)
    assert job.trigger.run_date == later.fire_at


async def test_cancel_removes_reminder_and_nudge_jobs_and_tolerates_missing(env) -> None:
    aps = ApsReminderScheduler(env.scheduler)
    aps.schedule_nudge(7, IN_AN_HOUR)
    assert jobs(env.scheduler)["nudge:7"].func_ref == NUDGE_JOB
    await aps.cancel(7)
    await aps.cancel(7)  # already gone: fine
    assert jobs(env.scheduler) == {}


async def test_reminder_service_keeps_rows_and_jobs_in_step(env, session, user_id) -> None:
    svc = ReminderService(session, user_id, env.reminders, now=NOW)
    created = await svc.create(ReminderCreate(message="Stretch", fire_at=IN_AN_HOUR))
    assert created.job_id == f"reminder:{created.id}"
    assert jobs(env.scheduler)[created.job_id].trigger.run_date == IN_AN_HOUR
    snoozed = await svc.snooze(created.id, 20)
    assert snoozed.status == "snoozed"
    assert jobs(env.scheduler)[created.job_id].trigger.run_date == NOW + timedelta(minutes=20)
    await svc.cancel(created.id)
    assert jobs(env.scheduler) == {}


# --- fire_reminder / fire_nudge -----------------------------------------------------------


async def test_fire_reminder_sends_marks_sent_and_schedules_one_nudge(
    env, session, user_id, channel
) -> None:
    task = await TaskRepo(session, user_id).create(
        TaskCreate(title="Submit SE", due_at=NOW + timedelta(minutes=60))
    )
    reminder = await ReminderRepo(session, user_id).create(
        ReminderCreate(message="Submit SE", fire_at=NOW, target_kind="task", target_id=task.id)
    )
    await fire_reminder(reminder.id)
    assert channel.sent == [("⏰ Submit SE at 11:00 AM — in 60 min.", buttons(reminder.id))]
    stored = await ReminderRepo(session, user_id).get(reminder.id)
    assert stored is not None and (stored.status, stored.telegram_message_id) == ("sent", 1001)
    nudge = jobs(env.scheduler)[f"nudge:{reminder.id}"]
    assert nudge.trigger.run_date == NOW + timedelta(minutes=15)
    await fire_reminder(reminder.id)  # a duplicate run sends nothing
    assert len(channel.sent) == 1


async def test_a_reminder_for_a_finished_task_is_dropped(env, session, user_id, channel) -> None:
    tasks = TaskRepo(session, user_id)
    task = await tasks.create(TaskCreate(title="Laundry"))
    await tasks.set_status(task.id, "done", NOW)
    reminder = await ReminderRepo(session, user_id).create(
        ReminderCreate(message="Laundry", fire_at=NOW, target_kind="task", target_id=task.id)
    )
    await fire_reminder(reminder.id)
    assert channel.sent == []
    stored = await ReminderRepo(session, user_id).get(reminder.id)
    assert stored is not None and stored.status == "cancelled"


async def test_fire_reminder_for_a_missing_row_does_nothing(env, channel) -> None:
    await fire_reminder(999_999)  # an orphan job whose row was rolled back
    assert channel.sent == []


async def test_nudge_goes_out_once_and_only_if_unanswered(env, session, user_id, channel) -> None:
    repo = ReminderRepo(session, user_id)
    open_one = await repo.create(ReminderCreate(message="Call mom", fire_at=NOW))
    answered = await repo.create(ReminderCreate(message="Water", fire_at=NOW))
    await repo.update(open_one.id, ReminderUpdate(status="sent"))
    await repo.update(answered.id, ReminderUpdate(status="acked"))
    await fire_nudge(open_one.id)
    await fire_nudge(open_one.id)
    await fire_nudge(answered.id)
    assert channel.sent == [
        ('Still on for "Call mom"? Reply done / snooze / skip.', buttons(open_one.id))
    ]
    stored = await repo.get(open_one.id)
    assert stored is not None and stored.nudge_sent


def test_reminder_text_says_when_and_flags_late_sends() -> None:
    base = ReminderDTO(
        id=1, target_kind="freeform", target_id=None, message="Call mom", fire_at=NOW,
        status="scheduled", nudge_sent=False, job_id=None, telegram_message_id=None,
        created_at=NOW, updated_at=NOW,
    )  # fmt: skip
    assert reminder_text(base, None, NOW) == "⏰ Call mom"
    assert reminder_text(base, NOW + timedelta(minutes=15), NOW) == (
        "⏰ Call mom at 10:15 AM — in 15 min."
    )
    assert reminder_text(base, None, NOW + timedelta(minutes=40)) == "⏰ (late) Call mom"


# --- reconcile at startup -----------------------------------------------------------------


async def test_reconcile_schedules_sends_late_expires_and_cleans_orphans(
    env, session, user_id
) -> None:
    repo = ReminderRepo(session, user_id)
    future = await repo.create(ReminderCreate(message="later", fire_at=IN_AN_HOUR))
    recent = await repo.create(
        ReminderCreate(message="recent", fire_at=NOW - timedelta(minutes=30))
    )
    stale = await repo.create(ReminderCreate(message="stale", fire_at=NOW - timedelta(hours=2)))
    sent = await repo.create(ReminderCreate(message="sent", fire_at=NOW - timedelta(minutes=5)))
    await repo.update(sent.id, ReminderUpdate(status="sent"))
    aps = env.reminders
    aps.schedule_nudge(sent.id, NOW + timedelta(minutes=10))  # still wanted
    await aps.schedule(stale.model_copy(update={"id": 999_998}))  # orphan: no such row

    counts = await reconcile(env)

    assert counts == {"scheduled": 1, "late": 1, "expired": 1, "orphans_removed": 1}
    found = jobs(env.scheduler)
    assert found[f"reminder:{future.id}"].trigger.run_date == IN_AN_HOUR
    assert found[f"reminder:{recent.id}"].trigger.run_date == NOW + timedelta(seconds=1)
    assert f"nudge:{sent.id}" in found and "reminder:999998" not in found
    expired = await repo.get(stale.id)
    assert expired is not None and expired.status == "expired"
