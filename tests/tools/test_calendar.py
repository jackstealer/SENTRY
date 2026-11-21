from datetime import UTC, datetime

from sentry_ai.db.repositories import CalendarRepo
from sentry_ai.db.schemas import CalendarEventUpsert

SYNCED = datetime(2026, 11, 22, tzinfo=UTC)


def event(gid: str, start: str, end: str) -> CalendarEventUpsert:
    return CalendarEventUpsert(
        gcal_event_id=gid,
        calendar_id="primary",
        title=gid,
        start_at=datetime.fromisoformat(start),
        end_at=datetime.fromisoformat(end),
        synced_at=SYNCED,
    )


async def test_get_calendar_events_returns_overlapping_events(call, session, user_id):
    await CalendarRepo(session, user_id).upsert_events(
        [
            event("standup", "2026-11-23T10:00+05:30", "2026-11-23T10:15+05:30"),
            event("talk", "2026-11-23T16:00+05:30", "2026-11-23T17:00+05:30"),
            event("friday", "2026-11-27T16:00+05:30", "2026-11-27T17:00+05:30"),
        ]
    )
    result = await call(
        "get_calendar_events", start="2026-11-23T00:00:00+05:30", end="2026-11-24T00:00:00+05:30"
    )
    assert result.ok and result.message == "2 event(s)."
    assert [(e["title"], e["start_at"]) for e in result.data["events"]] == [
        ("standup", "2026-11-23T10:00+05:30"),
        ("talk", "2026-11-23T16:00+05:30"),
    ]


async def test_get_calendar_events_rejects_bad_input(call):
    start = "2026-11-23T00:00:00+05:30"
    assert (await call("get_calendar_events", start=start, end=start)).error == "invalid_args"
    month = await call("get_calendar_events", start=start, end="2027-01-01T00:00:00+05:30")
    assert month.error == "invalid_args" and "31 days" in month.message
    naive = await call("get_calendar_events", start="2026-11-23T00:00:00", end=start)
    assert naive.error == "invalid_args"


async def test_late_night_event_belongs_to_its_ist_day_only(call, session, user_id):
    await CalendarRepo(session, user_id).upsert_events(
        [event("late", "2026-11-22T23:00+05:30", "2026-11-22T23:45+05:30")]
    )
    await CalendarRepo(session, "someone-else").upsert_events(
        [event("theirs", "2026-11-22T23:00+05:30", "2026-11-22T23:45+05:30")]
    )
    sunday = await call(
        "get_calendar_events", start="2026-11-22T00:00:00+05:30", end="2026-11-23T00:00:00+05:30"
    )
    monday = await call(
        "get_calendar_events", start="2026-11-23T00:00:00+05:30", end="2026-11-24T00:00:00+05:30"
    )
    assert [e["title"] for e in sunday.data["events"]] == ["late"]  # 17:30 UTC, same IST day
    assert monday.data["events"] == [] and "No calendar events" in monday.message
