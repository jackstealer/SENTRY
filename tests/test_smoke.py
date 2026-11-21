"""Phase 0 smoke: config loads, DB reachable, one cheap real LLM call (skipped without a key)."""

from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.agent import llm
from sentry_ai.config import get_settings


def _llm_configured() -> bool:
    settings = get_settings()
    return settings.openai_api_key is not None and bool(settings.openai_model_fast)


def test_config_loads() -> None:
    settings = get_settings()
    assert ZoneInfo(settings.user_tz).key == settings.user_tz
    assert settings.database_url.startswith("postgresql+asyncpg://")


async def test_db_reachable(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT 1")) == 1


@pytest.mark.skipif(
    not _llm_configured(), reason="set OPENAI_API_KEY and OPENAI_MODEL_FAST in .env"
)
async def test_one_cheap_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    rows: list[object] = []

    async def keep(row: object) -> None:  # don't commit a usage row into the test DB
        rows.append(row)

    monkeypatch.setattr(llm, "record_usage", keep)
    # usage, not content: a reasoning "fast" model may spend its tiny budget on reasoning
    response = await llm.chat(
        [{"role": "user", "content": "Reply with the single word OK."}],
        purpose="smoke",
        max_completion_tokens=16,
    )
    assert response.usage is not None
    assert response.usage.prompt_tokens > 0
    assert len(rows) == 1  # usage is recorded for every call
