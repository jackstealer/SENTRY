"""POST /debug/trigger/{job}: dev-only, runs a scheduled job now (interfaces/api/debug.py)."""

from collections.abc import Iterator

import httpx
import pytest

from sentry_ai.config import get_settings
from sentry_ai.interfaces.api.app import create_app


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t")


async def test_trigger_runs_the_job_now_and_force_repeats_it(env, channel) -> None:
    async with client() as http:
        first = await http.post("/debug/trigger/weekly_review")
        again = await http.post("/debug/trigger/weekly_review")
    assert first.status_code == 200 and first.json() == {"job": "weekly_review", "result": True}
    assert again.json()["result"] is True  # force (the default) skips the once-a-day claim
    assert len(channel.sent) == 2 and channel.sent[0][0].startswith("Week in review, ")


async def test_without_force_the_once_a_day_claim_applies(env, channel) -> None:
    async with client() as http:
        first = await http.post("/debug/trigger/evening_review?force=false")
        second = await http.post("/debug/trigger/evening_review?force=false")
    assert (first.json()["result"], second.json()["result"]) == (True, False)
    assert len(channel.sent) == 1


async def test_unknown_jobs_and_a_stopped_scheduler_are_explained(env) -> None:
    async with client() as http:
        unknown = await http.post("/debug/trigger/party")
    assert unknown.status_code == 404 and "morning_brief" in unknown.json()["detail"]
    from sentry_ai.scheduler import env as job_env

    job_env.install(None)
    async with client() as http:
        stopped = await http.post("/debug/trigger/morning_brief")
    assert stopped.status_code == 503


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENV", "prod")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_debug_routes_do_not_exist_outside_dev(prod: None) -> None:
    async with client() as http:
        response = await http.post("/debug/trigger/morning_brief")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
