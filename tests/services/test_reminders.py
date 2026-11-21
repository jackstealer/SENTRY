from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from sentry_ai.db.repositories import HabitRepo, PlanRepo, ReminderRepo, TaskRepo
from sentry_ai.db.schemas import HabitCreate, PlanBlockCreate, ReminderCreate, TaskCreate
from sentry_ai.services.reminders import (
    NoopReminderScheduler,
    ReminderError,
    ReminderScheduler,
    ReminderService,
)

NOW = datetime(2026, 11, 22, 6, 30, tzinfo=UTC)  # 12:00 IST


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[int, datetime] = {}

    async def schedule(self, reminder):  # type: ignore[no-untyped-def]
        self.jobs[reminder.id] = reminder.fire_at
        return f"job-{reminder.id}"

    async def cancel(self, reminder_id: int) -> None:
        self.jobs.pop(reminder_id, None)


@pytest.fixture
def jobs() -> Jobs:
    return Jobs()


@pytest.fixture
def service(session: AsyncSession, user_id: str, jobs: Jobs) -> ReminderService:
    return ReminderService(session, user_id, jobs, now=NOW)


def at(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


async def test_noop_scheduler_satisfies_the_protocol_and_logs(service: ReminderService) -> None:
    noop = NoopReminderScheduler()
    assert isinstance(noop, ReminderScheduler)
    plain = ReminderService(service.session, service.user_id, noop, now=NOW)
    with capture_logs() as logs:
        reminder = await plain.create(ReminderCreate(message="x", fire_at=at(5)))
        await plain.cancel(reminder.id)
    assert reminder.job_id is None
    assert [e["event"] for e in logs] == ["reminder_schedule_deferred", "reminder_cancel_deferred"]


async def test_create_refuses_now_and_the_past(service: ReminderService) -> None:
    for fire_at in (NOW, at(-1)):
        with pytest.raises(ReminderError, match="already passed") as info:
            await service.create(ReminderCreate(message="x", fire_at=fire_at))
        assert info.value.error == "invalid_time"


async def test_shift_moves_future_reminders_and_drops_ones_now_in_the_past(
    service: ReminderService, session: AsyncSession, user_id: str, jobs: Jobs
) -> None:
    task = await TaskRepo(session, user_id).create(TaskCreate(title="SE"))
    near = await service.create(
        ReminderCreate(message="a", fire_at=at(30), target_kind="task", target_id=task.id)
    )
    far = await service.create(
        ReminderCreate(message="b", fire_at=at(300), target_kind="task", target_id=task.id)
    )
    assert await service.shift_for("task", task.id, timedelta(minutes=-60)) == 2
    repo = ReminderRepo(session, user_id)
    moved, dropped = await repo.get(far.id), await repo.get(near.id)
    assert moved is not None and moved.fire_at == at(240) and jobs.jobs[far.id] == at(240)
    assert dropped is not None and dropped.status == "cancelled" and near.id not in jobs.jobs


async def test_ack_on_a_plan_block_marks_block_and_task(
    service: ReminderService, session: AsyncSession, user_id: str
) -> None:
    task = await TaskRepo(session, user_id).create(TaskCreate(title="DSA set"))
    plan = await PlanRepo(session, user_id).create_draft(
        date(2026, 11, 22),
        None,
        [PlanBlockCreate(start_at=at(60), end_at=at(120), kind="task", ref_id=task.id,
                         title="DSA set")],
    )  # fmt: skip
    [block] = plan.blocks
    reminder = await service.create(
        ReminderCreate(message="DSA", fire_at=at(50), target_kind="plan_block", target_id=block.id)
    )
    acked, effect = await service.ack(reminder.id, "done")
    assert acked.status == "acked"
    assert effect == "task 'DSA set' marked done"
    updated = await PlanRepo(session, user_id).get_block(block.id)
    assert updated is not None and updated.outcome == "done"
    done = await TaskRepo(session, user_id).get(task.id)
    assert done is not None and (done.status, done.completed_at) == ("done", NOW)


async def test_ack_on_a_habit_logs_today_and_on_freeform_does_nothing_else(
    service: ReminderService, session: AsyncSession, user_id: str
) -> None:
    habit = await HabitRepo(session, user_id).create(HabitCreate(name="Read", rrule="FREQ=DAILY"))
    linked = await service.create(
        ReminderCreate(message="Read", fire_at=at(5), target_kind="habit", target_id=habit.id)
    )
    assert (await service.ack(linked.id, "done"))[1] == "habit logged done for 2026-11-22"
    freeform = await service.create(ReminderCreate(message="x", fire_at=at(5)))
    acked, effect = await service.ack(freeform.id, "skip")
    assert (acked.status, effect) == ("skipped", None)


async def test_a_sent_reminder_can_still_be_snoozed(
    service: ReminderService, session: AsyncSession, user_id: str
) -> None:
    from sentry_ai.db.schemas import ReminderUpdate

    reminder = await service.create(ReminderCreate(message="x", fire_at=at(5)))
    await ReminderRepo(session, user_id).update(reminder.id, ReminderUpdate(status="sent"))
    snoozed = await service.snooze(reminder.id, 10)
    assert (snoozed.status, snoozed.fire_at, snoozed.nudge_sent) == ("snoozed", at(10), False)
