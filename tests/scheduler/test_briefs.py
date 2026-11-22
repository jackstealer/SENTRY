"""Fallback briefs: plain templates from DB data, for when the LLM is unavailable."""

from datetime import UTC, date, datetime, time

import pytest
import sqlalchemy as sa

from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import HabitRepo, PlanRepo, RoutineRepo, TaskRepo
from sentry_ai.db.schemas import (
    HabitCreate,
    PlanBlockCreate,
    PlanBlockUpdate,
    RoutineCreate,
    TaskCreate,
)
from sentry_ai.scheduler.briefs import DASH, fallback_text

from .conftest import NOW  # Mon 23 Nov, 10:00 IST


def ist(day: int, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-11-{day:02d}T{hhmm}:00+05:30")


@pytest.fixture
async def monday(session, user_id):  # type: ignore[no-untyped-def]
    await RoutineRepo(session, user_id).create(
        RoutineCreate(
            title="College",
            category="study",
            rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
            start_time=time(9),
            duration_min=300,
            active_from=date(2026, 11, 1),
        )
    )
    habit = await HabitRepo(session, user_id).create(
        HabitCreate(name="DSA practice", rrule="FREQ=DAILY", preferred_time=time(21))
    )
    # created long ago, so it is due today whatever the real clock says
    await session.execute(
        sa.update(Habit)
        .where(Habit.id == habit.id)
        .values(created_at=datetime(2026, 11, 1, tzinfo=UTC))
    )
    tasks = TaskRepo(session, user_id)
    return {
        "se": await tasks.create(
            TaskCreate(title="Submit SE", priority=1, due_at=ist(23, "17:00"))
        ),
        "laundry": await tasks.create(TaskCreate(title="Laundry", due_at=ist(23, "19:00"))),
        "habit": habit,
    }


async def test_morning_brief_template(session, user_id, monday) -> None:
    await TaskRepo(session, user_id).create(TaskCreate(title="Pay rent", due_at=ist(22, "10:00")))
    text = await fallback_text("morning_brief", session=session, user_id=user_id, now=NOW)
    assert text == (
        "Good morning, sir. Monday, 23 Nov.\n"
        "\n"
        "Fixed today:\n"
        f"• 9:00 AM{DASH}2:00 PM College\n"
        "\n"
        "Top 3:\n"
        "1. Submit SE — due 5:00 PM\n"  # due today at p1 ranks above overdue at p2
        "2. Pay rent — overdue\n"
        "3. Laundry — due 7:00 PM\n"
        "\n"
        "Habits due: DSA practice\n"
        "Overdue: Pay rent. Want me to move it?\n"
        "\n"
        "Want me to plan the open slots?"
    )


async def test_morning_brief_points_at_free_time_when_nothing_is_overdue(
    session, user_id, monday
) -> None:
    text = await fallback_text("morning_brief", session=session, user_id=user_id, now=NOW)
    assert f"Longest free stretch: 2:10 PM{DASH}11:00 PM." in text  # after College + buffer
    assert "Overdue" not in text


async def test_evening_review_template(session, user_id, monday) -> None:
    await TaskRepo(session, user_id).set_status(monday["se"].id, "done", NOW)
    evening = datetime(2026, 11, 23, 16, 30, tzinfo=UTC)  # 22:00 IST
    text = await fallback_text("evening_review", session=session, user_id=user_id, now=evening)
    assert text == (
        "Day wrap, 23 Nov.\n"
        "✅ Done: 1/2 tasks, 0/1 habits\n"
        "Not done: Laundry — roll to tomorrow?\n"
        "Habits not logged: DSA practice.\n"
        "Anything blocking tomorrow?"
    )


async def test_evening_review_clean_sweep(session, user_id, monday) -> None:
    tasks = TaskRepo(session, user_id)
    for task in (monday["se"], monday["laundry"]):
        await tasks.set_status(task.id, "done", NOW)
    await HabitRepo(session, user_id).log(monday["habit"].id, date(2026, 11, 23), "done")
    text = await fallback_text("evening_review", session=session, user_id=user_id, now=NOW)
    assert "✅ Done: 2/2 tasks, 1/1 habits\nClean sweep today.\n" in text


async def test_weekly_review_template(session, user_id) -> None:
    tasks = TaskRepo(session, user_id)
    done = await tasks.create(TaskCreate(title="a", due_at=ist(20, "10:00")))
    await tasks.set_status(done.id, "done", NOW)
    await tasks.create(TaskCreate(title="b", due_at=ist(21, "10:00")))
    plans = PlanRepo(session, user_id)
    block = PlanBlockCreate(
        start_at=ist(22, "08:00"), end_at=ist(22, "09:00"), kind="task", title="x"
    )
    plan = await plans.create_draft(date(2026, 11, 22), None, [block])
    await plans.confirm(plan.id)
    await plans.update_block(plan.blocks[0].id, PlanBlockUpdate(outcome="done"))
    sunday = datetime(2026, 11, 22, 14, 30, tzinfo=UTC)  # Sun 20:00 IST
    text = await fallback_text("weekly_review", session=session, user_id=user_id, now=sunday)
    assert text == (
        f"Week in review, 16 Nov{DASH}22 Nov.\n"
        "✅ Tasks: 1/2 done (50%)\n"
        "Habits: nothing tracked\n"
        "Rollovers: 0\n"
        "Plan: 1/1 planned blocks done\n"
        "\n"
        "What should next week focus on?"
    )


async def test_unknown_fallback_is_a_bug(session, user_id) -> None:
    with pytest.raises(ValueError, match="no fallback template"):
        await fallback_text("party", session=session, user_id=user_id, now=NOW)
