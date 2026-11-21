"""FastAPI app factory: health check now; debug, webhook and dashboard routes in later phases."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.db.session import get_engine
from sentry_ai.observability.logging import bind_trace, get_logger

log = get_logger(__name__)

HEALTH_DB_TIMEOUT_S = 3.0  # a hung DB must not hang the health check (Docker probes it)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="SENTRY", lifespan=lifespan)

    @app.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = bind_trace(path=request.url.path)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.get("/health")
    async def health(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> JSONResponse:
        try:
            async with asyncio.timeout(HEALTH_DB_TIMEOUT_S), engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # any DB failure (refused, auth, timeout) is "degraded", never a 500
            log.warning("health_db_failed", exc_info=True)
            return JSONResponse({"status": "degraded", "db": "error"}, status_code=503)
        return JSONResponse({"status": "ok", "db": "ok"})

    return app
