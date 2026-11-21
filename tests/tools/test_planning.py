from datetime import UTC, date, datetime, time

import pytest
import sqlalchemy as sa

from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import (
    HabitRepo,
    PlanRepo,
    PreferenceRepo,
    ReminderRepo,
    RoutineRepo,
    TaskRepo,
)
from sentry_ai.db.schemas import HabitCreate, RoutineCreate, TaskCreate

MON = date(2026, 11, 23)
WEEKDAYS = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
PREFS = {
    "no_tasks_before": {"time": "07:00"},
    "sleep_time": {"time": "23:30"},
    "quiet_hours": {"start": "23:30", "end": "06:30"},
    "deep_work_window": {"start": "21:00", "end": "23:00"},
    "default_reminder_min": {"minutes": 15},
}


def ist(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}+05:30"


@pytest.fixture
async def seeded(session, user_id):
    for key, value in PREFS.items():
        await PreferenceRepo(session, user_id).set(key, value)
    routines = RoutineRepo(session, user_id)
    anchor = date(2026, 11, 1)  # not the real creation day, so tests never depend on the clock
    college = await routines.create(
        RoutineCreate(title="College", category="study", rrule=WEEKDAYS, start_time=time(9),
                      duration_min=300, active_from=anchor)
    )  # fmt: skip
    lunch = await routines.create(
        RoutineCreate(title="Lunch", category="health", rrule="FREQ=DAILY", start_time=time(13, 30),
                      duration_min=45, is_fixed=False, active_from=anchor)
    )  # fmt: skip
    tasks = TaskRepo(session, user_id)
    se = await tasks.create(
        TaskCreate(title="SE assignment", priority=1, estimate_min=60,
                   due_at=datetime(2026, 11, 23, 11, 30, tzinfo=UTC))  # 17:00 IST
    )  # fmt: skip
    groceries = await tasks.create(TaskCreate(title="Groceries", priority=3))
    return {"college": college.id, "lunch": lunch.id, "se": se.id, "groceries": groceries.id}


def blocks_by_title(result):
    return {b["title"]: b for b in result.data["blocks"]}


async def propose(call, **args):
    result = await call("propose_day_plan", **({"date": MON.isoformat()} | args))
    assert result.ok, result.message
    return result


# --- propose_day_plan ---------------------------------------------------------------------


async def test_propose_saves_a_draft_around_fixed_blocks(call, seeded, session, user_id):
    result = await propose(call)
    blocks = blocks_by_title(result)
    assert {
        t: (b["kind"], b["start_at"][11:16], b["end_at"][11:16]) for t, b in blocks.items()
    } == {
        "SE assignment": ("task", "07:00", "08:00"),
        "Groceries": ("task", "08:10", "08:40"),
        "College": ("routine", "09:00", "14:00"),
        "Lunch": ("routine", "14:10", "14:55"),  # flexible, pushed past College + buffer
    }
    assert result.message.startswith(f"Draft plan {result.data['plan_id']} for Mon 23 Nov")
    assert result.data["overcommitted_by_min"] == 0
    plan = await PlanRepo(session, user_id).get(result.data["plan_id"])
    assert plan is not None and (plan.status, plan.local_date, len(plan.blocks)) == (
        "draft",
        MON,
        4,
    )


async def test_propose_rejects_bad_input(call, seeded):
    past = await call("propose_day_plan", date="2026-11-21")
    assert past.error == "invalid_args" and "over" in past.message
    empty = await call("propose_day_plan", available_windows=[{"start": "14:00", "end": "14:00"}])
    assert empty.error == "invalid_args"
    assert (await call("propose_day_plan", task_order=["SE"])).error == "invalid_args"


async def test_propose_after_midnight_ist_plans_the_new_local_day(call, seeded):
    at = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # 00:30 IST on Mon 23rd
    result = await call("propose_day_plan", now=at)
    assert result.data["local_date"] == "2026-11-23"  # not the UTC date (22nd)


async def test_propose_late_today_uses_only_what_is_left(call, seeded):
    at = datetime(2026, 11, 22, 17, 30, tzinfo=UTC)  # Sun 23:00 IST
    result = await call("propose_day_plan", now=at)
    blocks = blocks_by_title(result)
    assert blocks["Groceries"]["start_at"] == "2026-11-22T23:00+05:30"  # 30 min before sleep
    assert "SE assignment" not in blocks
    assert [u["ref_id"] for u in result.data["unplaced"]] == [seeded["se"]]
    assert result.data["overcommitted_by_min"] == 60
    # lunch (13:30) is long past: deferred, not counted as overcommitment
    assert [(d["title"], d["reason"]) for d in result.data["deferred"]] == [
        ("Lunch", "its usual time has passed")
    ]


async def test_propose_respects_windows_order_and_habits(call, seeded, session, user_id):
    habit = await HabitRepo(session, user_id).create(
        HabitCreate(name="DSA practice", rrule="FREQ=DAILY", preferred_time=time(21))
    )
    await session.execute(
        sa.update(Habit)
        .where(Habit.id == habit.id)
        .values(created_at=datetime(2026, 11, 1, tzinfo=UTC))
    )
    result = await propose(
        call,
        available_windows=[{"start": "15:00", "end": "18:00"}],
        task_order=[seeded["groceries"]],
    )
    blocks = blocks_by_title(result)
    starts = {title: b["start_at"][11:16] for title, b in blocks.items() if b["kind"] != "routine"}
    assert blocks["Lunch"]["start_at"][11:16] == "15:00"  # flexible: 1.5 h late, in the window
    assert starts == {"Groceries": "15:55", "SE assignment": "16:35"}  # task_order first
    # DSA practice (21:00) cannot move 2 h+ into the offered window
    assert [u["title"] for u in result.data["unplaced"]] == ["DSA practice"]


# --- confirm_day_plan ---------------------------------------------------------------------


async def test_confirm_sets_reminders_and_schedules_tasks(
    call, seeded, session, user_id, scheduler
):
    draft = await propose(call)
    result = await call("confirm_day_plan", plan_id=draft.data["plan_id"])
    assert result.ok and result.data["reminders_created"] == 2  # the two task blocks
    plan = await PlanRepo(session, user_id).get(draft.data["plan_id"])
    assert plan is not None and plan.status == "confirmed"
    se_block = next(b for b in plan.blocks if b.title == "SE assignment")
    [reminder] = await ReminderRepo(session, user_id).for_target("plan_block", se_block.id)
    assert reminder.fire_at == datetime(2026, 11, 23, 1, 15, tzinfo=UTC)  # 06:45 IST, 15 min ahead
    assert reminder.message == "SE assignment at 07:00" and reminder.id in scheduler.jobs
    task = await TaskRepo(session, user_id).get(seeded["se"])
    assert task is not None and task.scheduled_start == se_block.start_at


async def test_confirming_a_new_plan_replaces_the_old_one(
    call, seeded, session, user_id, scheduler
):
    first = await propose(call)
    await call("confirm_day_plan", plan_id=first.data["plan_id"])
    second = await propose(call, task_order=[seeded["groceries"]])
    result = await call("confirm_day_plan", plan_id=second.data["plan_id"])
    assert result.data["replaced_plan_id"] == first.data["plan_id"]
    old = await PlanRepo(session, user_id).get(first.data["plan_id"])
    assert old is not None and old.status == "superseded"
    old_reminders = [
        r
        for b in old.blocks
        for r in await ReminderRepo(session, user_id).for_target("plan_block", b.id)
    ]
    assert {r.status for r in old_reminders} == {"cancelled"}
    assert len(scheduler.jobs) == 2  # only the new plan's reminders remain
    again = await call("confirm_day_plan", plan_id=first.data["plan_id"])
    assert again.error == "invalid_state"


async def test_confirm_rejects_bad_input(call, seeded):
    assert (await call("confirm_day_plan", plan_id=999_999)).error == "not_found"
    assert (await call("confirm_day_plan")).error == "invalid_args"
    draft = await propose(call)
    assert (await call("confirm_day_plan", plan_id=draft.data["plan_id"], sync="yes")).error == (
        "invalid_args"
    )


async def test_confirm_late_evening_skips_reminders_already_due(call, seeded, session, user_id):
    at = datetime(2026, 11, 22, 17, 30, tzinfo=UTC)  # Sun 23:00 IST; Groceries at 23:00
    draft = await call("propose_day_plan", now=at)
    result = await call("confirm_day_plan", plan_id=draft.data["plan_id"], now=at)
    assert result.ok and result.data["reminders_created"] == 0  # 22:45 has passed
    assert await ReminderRepo(session, user_id).list_pending() == []


# --- move_block ---------------------------------------------------------------------------


async def confirmed_plan(call):
    draft = await propose(call)
    await call("confirm_day_plan", plan_id=draft.data["plan_id"])
    return blocks_by_title(draft)


async def test_move_block_moves_reminder_and_task_schedule(
    call, seeded, session, user_id, scheduler
):
    blocks = await confirmed_plan(call)
    se = blocks["SE assignment"]
    result = await call("move_block", block_id=se["id"], new_start=ist(MON, "15:30"))
    assert result.ok and result.message == "Moved 'SE assignment' to 15:30-16:30."
    [reminder] = await ReminderRepo(session, user_id).for_target("plan_block", se["id"])
    assert reminder.fire_at == datetime(2026, 11, 23, 9, 45, tzinfo=UTC)  # 15:15 IST
    assert scheduler.jobs[reminder.id] == reminder.fire_at
    task = await TaskRepo(session, user_id).get(seeded["se"])
    assert task is not None and task.scheduled_start == datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("title", "new_start", "error", "text"),
    [
        ("SE assignment", ist(MON, "13:30"), "conflict", "overlap 'College'"),
        ("SE assignment", ist(MON, "23:00"), "conflict", "outside your planning hours"),
        ("SE assignment", ist(MON, "06:00"), "conflict", "outside your planning hours"),
        ("SE assignment", "2026-11-23T15:00:00", "invalid_args", "new_start"),
        ("SE assignment", ist(date(2026, 11, 22), "11:00"), "invalid_time", "passed"),
        ("College", ist(MON, "15:00"), "invalid_state", "fixed routine"),
    ],
)
async def test_move_block_rejects_bad_moves(call, seeded, title, new_start, error, text):
    blocks = await confirmed_plan(call)
    result = await call("move_block", block_id=blocks[title]["id"], new_start=new_start)
    assert (result.ok, result.error) == (False, error)
    assert text in result.message


async def test_move_block_accepts_utc_input_and_flexible_routines(call, seeded):
    blocks = await confirmed_plan(call)
    assert (await call("move_block", block_id=999_999, new_start=ist(MON, "15:00"))).error == (
        "not_found"
    )
    # 09:30Z is 15:00 IST; the reply speaks local time
    moved = await call(
        "move_block", block_id=blocks["Lunch"]["id"], new_start="2026-11-23T09:30:00Z"
    )
    assert moved.ok and moved.message == "Moved 'Lunch' to 15:00-15:45."
