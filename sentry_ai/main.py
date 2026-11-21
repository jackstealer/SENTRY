"""Process entry point: one asyncio loop for the API (scheduler, Telegram join in Phases 4-5).

Run with: uv run python -m sentry_ai.main
"""

import asyncio

import uvicorn

from sentry_ai.config import Settings, get_settings
from sentry_ai.interfaces.api.app import create_app
from sentry_ai.observability.logging import configure_logging, get_logger

log = get_logger("sentry_ai.main")  # not __name__: under `python -m` that is "__main__"


def build_server(settings: Settings) -> uvicorn.Server:
    # log_config=None: keep our JSON logging instead of uvicorn's own formatters
    config = uvicorn.Config(
        create_app(), host=settings.api_host, port=settings.api_port, log_config=None
    )
    return uvicorn.Server(config)


async def serve() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("sentry_starting", env=settings.env, host=settings.api_host, port=settings.api_port)
    await build_server(settings).serve()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
