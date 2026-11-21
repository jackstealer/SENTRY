from datetime import UTC, date, datetime, time

from sentry_ai.db.repositories import RoutineRepo
from sentry_ai.db.schemas import RoutineCreate
from sentry_ai.services.routines import expand_routines

GYM = {"title": "Gym", "category": "fitness", "rrule": "FREQ=DAILY", "start_time": "18:00"}


async def new_routine(call, **args):
    result = await call("create_routine", **({"duration_min": 90} | GYM | args))
    assert result.ok, result.message
    return result.data["routine"]["id"]


# --- create_routine -----------------------------------------------------------------------


async def test_create_routine_stores_it(call, session, user_id):
    result = await call("create_routine", duration_min=90, is_fixed=False, **GYM)
    assert result.ok and result.message == f"Added routine {result.data['routine']['id']} 'Gym'."
    routine = await RoutineRepo(session, user_id).get_by_title("gym")
    assert routine is not None
    assert (routine.start_time, routine.duration_min, routine.is_fixed) == (time(18), 90, False)


async def test_create_routine_rejects_bad_input_and_duplicates(call):
    assert (await call("create_routine", duration_min=0, **GYM)).error == "invalid_args"
    assert (await call("create_routine", duration_min=30, **GYM | {"category": "nap"})).error == (
        "invalid_args"
    )
    window = await call(
        "create_routine",
        duration_min=30,
        active_from="2026-12-10",
        active_until="2026-12-01",
        **GYM,
    )
    assert window.error == "invalid_args"
    await new_routine(call)
    assert (await call("create_routine", duration_min=30, **GYM)).error == "duplicate"


async def test_late_routine_runs_past_midnight_ist(call, session, user_id):
    routine_id = await new_routine(call, start_time="23:30", title="Night study", category="study")
    routine = await RoutineRepo(session, user_id).get(routine_id)
    assert routine is not None
    [block] = expand_routines([routine], date(2026, 11, 22))
    assert block.start_at == datetime(2026, 11, 22, 18, 0, tzinfo=UTC)  # 23:30 IST
    assert block.end_at == datetime(2026, 11, 22, 19, 30, tzinfo=UTC)  # 01:00 IST on the 23rd


# --- update_routine -----------------------------------------------------------------------


async def test_update_routine_changes_only_given_fields(call, session, user_id):
    routine_id = await new_routine(call)
    result = await call("update_routine", routine_id=routine_id, start_time="19:00")
    assert result.ok and result.message.endswith("start_time.")
    routine = await RoutineRepo(session, user_id).get(routine_id)
    assert routine is not None and (routine.start_time, routine.duration_min) == (time(19), 90)


async def test_update_routine_rejects_bad_input(call):
    routine_id = await new_routine(call)
    assert (await call("update_routine", routine_id=routine_id)).error == "invalid_args"
    assert (await call("update_routine", routine_id=routine_id, rrule=None)).error == (
        "invalid_args"
    )
    assert (await call("update_routine", routine_id=routine_id, duration_min=-5)).error == (
        "invalid_args"
    )
    assert (await call("update_routine", routine_id=999_999, duration_min=5)).error == ("not_found")


async def test_update_routine_active_window_is_by_local_date(call, session, user_id):
    routine_id = await new_routine(call)
    await call("update_routine", routine_id=routine_id, active_until="2026-11-22")
    active_23 = await RoutineRepo(session, user_id).list_active(on=date(2026, 11, 23))
    active_22 = await RoutineRepo(session, user_id).list_active(on=date(2026, 11, 22))
    assert [r.id for r in active_22] == [routine_id]
    assert active_23 == []


# --- list_routines ------------------------------------------------------------------------


async def test_list_routines_shows_local_times(call):
    await new_routine(call)
    await new_routine(call, title="College", category="study", start_time="09:00")
    result = await call("list_routines")
    assert result.ok and result.message == "2 routine(s)."
    assert [(r["title"], r["start_time"]) for r in result.data["routines"]] == [
        ("College", "09:00"),
        ("Gym", "18:00"),
    ]


async def test_list_routines_rejects_arguments(call):
    assert (await call("list_routines", active=True)).error == "invalid_args"


async def test_list_routines_hides_archived_and_other_users(call, session, user_id):
    routine_id = await new_routine(call)
    await RoutineRepo(session, user_id).archive(routine_id)
    await RoutineRepo(session, "someone-else").create(
        RoutineCreate.model_validate(GYM | {"duration_min": 30})
    )
    result = await call("list_routines")
    assert result.data["routines"] == []
