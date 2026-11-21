import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.db.session import get_engine
from sentry_ai.interfaces.api import app as app_module
from sentry_ai.interfaces.api.app import create_app


def client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class RefusingEngine:
    def connect(self) -> Any:
        raise ConnectionRefusedError("db down")


class HangingEngine:
    @asynccontextmanager
    async def connect(self) -> AsyncIterator[None]:
        await asyncio.sleep(3600)
        yield


async def test_health_ok_when_db_reachable(engine: AsyncEngine) -> None:
    async with client_for(create_app()) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}
    trace_id = resp.headers["x-trace-id"]
    assert len(trace_id) == 32 and int(trace_id, 16) >= 0


async def test_health_degraded_when_db_refuses() -> None:
    app = create_app()
    app.dependency_overrides[get_engine] = RefusingEngine
    async with client_for(app) as client:
        resp = await client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "error"}


async def test_health_degraded_when_db_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "HEALTH_DB_TIMEOUT_S", 0.05)
    app = create_app()
    app.dependency_overrides[get_engine] = HangingEngine
    async with client_for(app) as client:
        resp = await asyncio.wait_for(client.get("/health"), timeout=5)
    assert resp.status_code == 503


async def test_each_request_gets_its_own_trace_id(engine: AsyncEngine) -> None:
    async with client_for(create_app()) as client:
        ids = {(await client.get("/health")).headers["x-trace-id"] for _ in range(3)}
    assert len(ids) == 3
