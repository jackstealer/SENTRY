import asyncio
import socket

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.config import get_settings
from sentry_ai.main import build_server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_server_boots_and_serves_health_over_real_http(engine: AsyncEngine) -> None:
    settings = get_settings().model_copy(update={"api_port": free_port()})
    server = build_server(settings)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:  # noqa: ASYNC110 - uvicorn exposes a bool, not an Event
                await asyncio.sleep(0.05)
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://127.0.0.1:{settings.api_port}/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "db": "ok"}
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
