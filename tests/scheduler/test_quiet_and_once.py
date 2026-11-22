from datetime import UTC, datetime, time, timedelta

import pytest

from sentry_ai.db.repositories import PreferenceRepo, ReminderRepo
from sentry_ai.db.schemas import ReminderCreate, ReminderUpdate
from sentry_ai.scheduler.proactive import send_proactive
from sentry_ai.scheduler.reminder_jobs import fire_nudge, fire_reminder
from sentry_ai.services.quiet_hours import quiet_until

QUIET = (time(23, 30), time(6, 30))
LATE_NIGHT = datetime(2026, 11, 23, 18, 15, tzinfo=UTC)  # Mon 23:45 IST
QUIET_ENDS = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)  # Tue 06:30 IST
EARLY = datetime(2026, 11, 23, 0, 30, tzinfo=UTC)  # Mon 06:00 IST


def jobs(scheduler):  # type: ignore[no-untyped-def]
    return {job.id: job for job in scheduler.get_jobs()}


async def quiet_prefs(session, user_id) -> None:  # type: ignore[no-untyped-def]
    await PreferenceRepo(session, user_id).set("quiet_hours", {"start": "23:30", "end": "06:30"})


# --- quiet_until --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "until"),
    [
        (LATE_NIGHT, QUIET_ENDS),  # before midnight: ends tomorrow morning
        (EARLY, datetime(2026, 11, 23, 1, 0, tzinfo=UTC)),  # after midnight: ends this morning
        (datetime(2026, 11, 23, 4, 30, tzinfo=UTC), None),  # 10:00: not quiet
        (datetime(2026, 11, 23, 1, 0, tzinfo=UTC), None),  # exactly 06:30: over
    ],
)
def test_quiet_until_handles_windows_across_midnight(now: datetime, until: datetime | None) -> None:
    assert quiet_until(now, QUIET) == until


def test_no_quiet_hours_means_never_quiet() -> None:
    assert quiet_until(LATE_NIGHT, None) is None


# --- reminders and nudges are held, not sent ----------------------------------------------


async def test_a_reminder_inside_quiet_hours_is_held_until_they_end(
    env, session, user_id, channel, clock
) -> None:
    await quiet_prefs(session, user_id)
    clock[0] = LATE_NIGHT
    reminder = await ReminderRepo(session, user_id).create(
        ReminderCreate(message="Stretch", fire_at=LATE_NIGHT)
    )
    await fire_reminder(reminder.id)
    assert channel.sent == []
    held = await ReminderRepo(session, user_id).get(reminder.id)
    assert held is not None and (held.status, held.fire_at) == ("scheduled", QUIET_ENDS)
    assert jobs(env.scheduler)[f"reminder:{reminder.id}"].trigger.run_date == QUIET_ENDS
    clock[0] = QUIET_ENDS  # the held job runs
    await fire_reminder(reminder.id)
    assert [text for text, _ in channel.sent] == ["⏰ Stretch"]


async def test_a_nudge_inside_quiet_hours_is_held(env, session, user_id, channel, clock) -> None:
    await quiet_prefs(session, user_id)
    repo = ReminderRepo(session, user_id)
    reminder = await repo.create(ReminderCreate(message="Stretch", fire_at=LATE_NIGHT))
    await repo.update(reminder.id, ReminderUpdate(status="sent"))
    clock[0] = LATE_NIGHT
    await fire_nudge(reminder.id)
    assert channel.sent == []
    assert jobs(env.scheduler)[f"nudge:{reminder.id}"].trigger.run_date == QUIET_ENDS


# --- proactive messages: once per day, quiet hours respected ------------------------------


async def build() -> str:
    return "Good morning, sir."


async def test_a_proactive_message_goes_out_once_per_local_day(env, channel, clock) -> None:
    job = "sentry_ai.services.time:now_utc"  # any importable no-arg function: never run here
    assert await send_proactive(env, "morning_brief", build, job_ref=job)
    assert not await send_proactive(env, "morning_brief", build, job_ref=job)  # restart, misfire
    clock[0] = clock[0] + timedelta(days=1)
    assert await send_proactive(env, "morning_brief", build, job_ref=job)
    assert [text for text, _ in channel.sent] == ["Good morning, sir."] * 2


async def test_a_failed_send_releases_the_claim_so_a_retry_works(env, channel) -> None:
    async def broken() -> str:
        raise ConnectionError("telegram down")

    job = "sentry_ai.services.time:now_utc"  # any importable no-arg function: never run here
    with pytest.raises(ConnectionError):
        await send_proactive(env, "morning_brief", broken, job_ref=job)
    assert await send_proactive(env, "morning_brief", build, job_ref=job)
    assert len(channel.sent) == 1


async def test_a_brief_in_quiet_hours_is_held_or_skipped(env, session, user_id, channel, clock):  # type: ignore[no-untyped-def]
    await quiet_prefs(session, user_id)
    job = "sentry_ai.services.time:now_utc"  # any importable no-arg function: never run here
    clock[0] = EARLY  # 06:00, quiet until 06:30 the same morning: hold it
    assert not await send_proactive(env, "morning_brief", build, job_ref=job)
    held = jobs(env.scheduler)["morning_brief:held"]
    assert held.trigger.run_date == datetime(2026, 11, 23, 1, 0, tzinfo=UTC)
    clock[0] = LATE_NIGHT  # 23:45: quiet until tomorrow, a review would be stale: skip
    assert not await send_proactive(env, "evening_review", build, job_ref=job)
    assert "evening_review:held" not in jobs(env.scheduler)
    assert channel.sent == []


async def test_force_skips_quiet_hours_and_the_claim(env, session, user_id, channel, clock) -> None:
    await quiet_prefs(session, user_id)
    clock[0] = LATE_NIGHT
    job = "sentry_ai.services.time:now_utc"  # any importable no-arg function: never run here
    assert await send_proactive(env, "morning_brief", build, job_ref=job, force=True)
    assert await send_proactive(env, "morning_brief", build, job_ref=job, force=True)
    assert len(channel.sent) == 2
