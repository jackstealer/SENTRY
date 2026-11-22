from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import sqlalchemy as sa
from structlog.testing import capture_logs

from sentry_ai.agent import llm
from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import HabitRepo, ReminderRepo, RoutineRepo
from sentry_ai.db.schemas import HabitCreate, RoutineCreate
from sentry_ai.scheduler.jobs import (
    evening_review,
    morning_brief,
    routine_materialize,
    schedule_daily_jobs,
    weekly_review,
)
from sentry_ai.scheduler.reminder_jobs import fire_reminder
from sentry_ai.tools.registry import INTENTS


def jobs(scheduler):  # type: ignore[no-untyped-def]
    return {job.id: job for job in scheduler.get_jobs()}


# --- briefs written by the agent ----------------------------------------------------------


async def test_morning_brief_is_written_by_the_agent_and_sent_once(agent_env, fake_llm, channel):  # type: ignore[no-untyped-def]
    fake_llm.say("Good morning, sir. Monday, 23 Nov. Nothing fixed today.")
    assert await morning_brief()
    assert await morning_brief() is False  # claimed for today: a second run sends nothing
    assert channel.sent == [("Good morning, sir. Monday, 23 Nov. Nothing fixed today.", [])]
    [call] = fake_llm.calls
    assert call["purpose"] == "act"  # proactive turns skip the routing call
    assert {t["function"]["name"] for t in call["tools"]} == set(INTENTS["query"])
    instruction = call["messages"][-1]["content"]
    assert instruction.startswith("[Scheduled: morning brief for Monday, 23 Nov, 10:00 AM]")


async def test_evening_review_uses_the_review_tools(agent_env, fake_llm, channel) -> None:
    fake_llm.say("Day wrap, 23 Nov. All done.")
    assert await evening_review()
    assert channel.sent[0][0] == "Day wrap, 23 Nov. All done."
    tools = {t["function"]["name"] for t in fake_llm.calls[0]["tools"]}
    assert tools == set(INTENTS["review"])


# --- the fallback when the LLM is unavailable (ROADMAP 4.9) --------------------------------


class BrokenClient:
    """An OpenAI client whose key is wrong: every request is rejected with 401."""

    def __init__(self) -> None:
        async def create(**kwargs: Any) -> Any:
            response = httpx.Response(401, request=httpx.Request("POST", "https://api.invalid"))
            raise openai.AuthenticationError("Incorrect API key", response=response, body=None)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


async def test_with_a_broken_openai_key_the_morning_brief_still_arrives(
    agent_env, channel, monkeypatch
) -> None:
    monkeypatch.setattr(llm, "get_client", lambda: BrokenClient())
    monkeypatch.setattr(llm, "model_for", lambda tier: "any-model")
    monkeypatch.setattr(llm, "candidates", lambda tier: ["any-model"])
    with capture_logs() as logs:
        assert await morning_brief()
    [(text, _)] = channel.sent
    assert text.startswith("Good morning, sir. Monday, 23 Nov.\n\nFixed today:\n")
    assert text.endswith("Want me to plan the open slots?")
    fallback = [e for e in logs if e["event"] == "proactive_fallback"]
    assert fallback and fallback[0]["reason"] == "llm_unavailable"


async def test_without_an_agent_the_weekly_review_is_the_template(env, channel) -> None:
    assert env.graph is None
    assert await weekly_review()
    assert channel.sent[0][0].startswith("Week in review, ")


# --- routine_materialize ------------------------------------------------------------------


async def test_routine_materialize_creates_todays_reminders_once(
    env, session, user_id, channel, clock
) -> None:
    routines = RoutineRepo(session, user_id)
    for title, at, lead in (
        ("Gym", time(18), 15),
        ("College", time(9), 30),
        ("Lunch", time(13), None),
    ):
        await routines.create(
            RoutineCreate(title=title, category="other", rrule="FREQ=DAILY", start_time=at,
                          duration_min=60, remind_before_min=lead, active_from=date(2026, 11, 1))
        )  # fmt: skip
    habit = await HabitRepo(session, user_id).create(
        HabitCreate(name="DSA practice", rrule="FREQ=DAILY", preferred_time=time(21),
                    remind_before_min=10)
    )  # fmt: skip
    await session.execute(
        sa.update(Habit)
        .where(Habit.id == habit.id)
        .values(created_at=datetime(2026, 11, 1, tzinfo=UTC))
    )
    assert await routine_materialize() == 2  # College's 08:30 reminder has passed; Lunch has none
    assert await routine_materialize() == 0  # running again only tops up
    pending = await ReminderRepo(session, user_id).list_pending()
    assert [(r.message, r.fire_at) for r in pending] == [
        ("Gym", datetime(2026, 11, 23, 12, 15, tzinfo=UTC)),  # 17:45 IST
        ("DSA practice", datetime(2026, 11, 23, 15, 20, tzinfo=UTC)),  # 20:50 IST
    ]
    assert {f"reminder:{r.id}" for r in pending} <= set(jobs(env.scheduler))
    clock[0] = pending[0].fire_at
    await fire_reminder(pending[0].id)
    assert channel.sent[0][0] == "⏰ Gym at 6:00 PM — in 15 min."


# --- daily jobs follow preferences --------------------------------------------------------


async def test_daily_jobs_use_preferred_times_and_move_when_they_change(env) -> None:
    prefs = {"wake_time": {"time": "06:30"}, "weekly_review": {"day": "SA", "time": "19:00"}}
    assert set(schedule_daily_jobs(env.scheduler, prefs)) == {
        "morning_brief", "evening_review", "weekly_review", "routine_materialize",
    }  # fmt: skip
    found = jobs(env.scheduler)
    assert str(found["morning_brief"].trigger) == "cron[hour='6', minute='30']"
    assert str(found["evening_review"].trigger) == "cron[hour='22', minute='0']"  # default
    assert str(found["weekly_review"].trigger) == "cron[day_of_week='sat', hour='19', minute='0']"
    assert found["morning_brief"].misfire_grace_time == 2 * 3600
    assert schedule_daily_jobs(env.scheduler, prefs) == []  # restart: nothing changes
    moved = schedule_daily_jobs(env.scheduler, prefs | {"wake_time": {"time": "07:15"}})
    assert moved == ["morning_brief"]
    assert str(jobs(env.scheduler)["morning_brief"].trigger) == "cron[hour='7', minute='15']"
