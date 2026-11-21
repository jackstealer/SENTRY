from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.db.repositories import PreferenceRepo, SystemRepo
from sentry_ai.db.schemas import LLMUsageCreate


@pytest.fixture
def prefs(session: AsyncSession, user_id: str) -> PreferenceRepo:
    return PreferenceRepo(session, user_id)


@pytest.fixture
def system(session: AsyncSession, user_id: str) -> SystemRepo:
    return SystemRepo(session, user_id)


async def test_preference_set_is_an_upsert(prefs: PreferenceRepo) -> None:
    first = await prefs.set("wake_time", {"time": "06:30"})
    assert (first.key, first.value) == ("wake_time", {"time": "06:30"})
    second = await prefs.set("wake_time", {"time": "07:00"})
    assert second.value == {"time": "07:00"}
    assert await prefs.get("wake_time") == second
    assert await prefs.get("missing") is None


async def test_preferences_all_delete_and_scoping(
    prefs: PreferenceRepo, session: AsyncSession
) -> None:
    await prefs.set("wake_time", {"time": "06:30"})
    await prefs.set("quiet_hours", {"start": "23:30", "end": "06:30"})
    assert await prefs.all() == {
        "wake_time": {"time": "06:30"},
        "quiet_hours": {"start": "23:30", "end": "06:30"},
    }
    other = PreferenceRepo(session, "someone-else")
    assert await other.all() == {}
    assert not await other.delete("wake_time")
    assert await prefs.delete("wake_time")
    assert not await prefs.delete("wake_time")
    assert set(await prefs.all()) == {"quiet_hours"}


async def test_proactive_run_is_claimed_once_per_job_and_day(system: SystemRepo) -> None:
    day = date(2026, 11, 22)
    assert await system.claim_proactive_run("morning_brief", day)
    assert not await system.claim_proactive_run("morning_brief", day)  # restart: no duplicate
    assert await system.claim_proactive_run("morning_brief", day + timedelta(days=1))
    assert await system.claim_proactive_run("evening_review", day)


async def test_llm_usage_round_trip(system: SystemRepo, session: AsyncSession) -> None:
    usage = await system.record_llm_usage(
        LLMUsageCreate(
            trace_id="t-1",
            model="fast-model",
            purpose="route",
            prompt_tokens=120,
            completion_tokens=8,
            latency_ms=410,
            est_cost_usd=Decimal("0.000123"),
        )
    )
    assert (usage.model, usage.est_cost_usd) == ("fast-model", Decimal("0.000123"))
    now = await session.scalar(sa.select(sa.func.now()))
    assert now is not None
    hour = timedelta(hours=1)
    assert [u.id for u in await system.llm_usage_between(now - hour, now + hour)] == [usage.id]
    assert await system.llm_usage_between(datetime(2020, 1, 1, tzinfo=UTC), now - hour) == []
