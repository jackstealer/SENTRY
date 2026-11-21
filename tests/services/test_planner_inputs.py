from datetime import UTC, date, datetime, time

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import CalendarRepo, HabitRepo, RoutineRepo, TaskRepo
from sentry_ai.db.schemas import CalendarEventUpsert, HabitCreate, RoutineCreate, TaskCreate
from sentry_ai.services.planner_inputs import gather

MON = date(2026, 11, 23)
SYNCED = datetime(2026, 11, 22, tzinfo=UTC)


def event(gid: str, start: datetime, end: datetime, **extra: bool) -> CalendarEventUpsert:
    return CalendarEventUpsert(
        gcal_event_id=gid, calendar_id="primary", title=gid, start_at=start, end_at=end,
        synced_at=SYNCED, **extra,
    )  # fmt: skip


async def test_gather_collects_what_touches_the_day(session: AsyncSession, user_id: str) -> None:
    routines = RoutineRepo(session, user_id)
    await routines.create(
        RoutineCreate(title="Sleep", category="health", rrule="FREQ=DAILY", start_time=time(23),
                      duration_min=8 * 60, active_from=date(2026, 11, 1))
    )  # fmt: skip
    calendar = CalendarRepo(session, user_id)
    talk = datetime(2026, 11, 23, 10, 30, tzinfo=UTC)  # 16:00 IST
    await calendar.upsert_events(
        [
            event("talk", talk, talk.replace(hour=11, minute=30)),
            event("holiday", SYNCED.replace(day=23), SYNCED.replace(day=24), all_day=True),
            event("mine", talk, talk.replace(hour=11), is_sentry_owned=True),
        ]
    )
    habits = HabitRepo(session, user_id)
    for name in ("Read", "Stretch"):
        habit = await habits.create(
            HabitCreate(name=name, rrule="FREQ=DAILY", preferred_time=time(21))
        )
        await session.execute(
            sa.update(Habit).where(Habit.id == habit.id).values(created_at=SYNCED.replace(day=1))
        )
    stretch = await habits.get_by_name("Stretch")
    assert stretch is not None
    await habits.log(stretch.id, MON, "done")
    await TaskRepo(session, user_id).create(TaskCreate(title="SE"))

    inputs = await gather(MON, session=session, user_id=user_id)

    sleeps = sorted(b.start for b in inputs.busy if b.title == "Sleep")
    assert sleeps[0] == datetime(2026, 11, 22, 17, 30, tzinfo=UTC)  # Sunday night, spills into Mon
    assert [b.title for b in inputs.busy if b.kind == "calendar_event"] == ["talk"]
    assert [f.title for f in inputs.flexible] == ["Read"]  # Stretch is already logged
    assert [(t.title, t.minutes) for t in inputs.tasks] == [("SE", 30)]  # default estimate
