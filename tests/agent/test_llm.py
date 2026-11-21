from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
import sqlalchemy as sa
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from structlog.contextvars import clear_contextvars
from structlog.testing import capture_logs
from tenacity import wait_none

from sentry_ai.agent import llm
from sentry_ai.config import get_settings
from sentry_ai.db.models import LLMUsage
from sentry_ai.db.schemas import LLMUsageCreate
from sentry_ai.observability.logging import bind_trace

COMPLETION = ChatCompletion.model_validate(
    {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "fast-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "OK"},
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }
)
EMBEDDING = CreateEmbeddingResponse.model_validate(
    {
        "object": "list",
        "model": "embed-model",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }
)


class FakeEndpoint:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, chat: FakeEndpoint | None = None, emb: FakeEndpoint | None = None) -> None:
        self.chat = SimpleNamespace(completions=chat or FakeEndpoint(COMPLETION))
        self.embeddings = emb or FakeEndpoint(EMBEDDING)


class RateLimited(Exception):
    status_code = 429


REAL_GET_CLIENT = llm.get_client  # tests monkeypatch llm.get_client; keep the cached original


def _reset_caches() -> None:
    get_settings.cache_clear()
    REAL_GET_CLIENT.cache_clear()


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("OPENAI_MODEL_FAST", "fast-model")
    monkeypatch.setenv("OPENAI_MODEL_REASONING", "reasoning-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMS", "1536")
    _reset_caches()
    yield monkeypatch
    _reset_caches()  # never leak the fake key into later tests (smoke test checks for a key)


@pytest.fixture(autouse=True)
def usage(monkeypatch: pytest.MonkeyPatch) -> list[LLMUsageCreate]:
    """Collect llm_usage rows in memory instead of writing them to the DB."""
    rows: list[LLMUsageCreate] = []

    async def sink(row: LLMUsageCreate) -> None:
        rows.append(row)

    monkeypatch.setattr(llm, "record_usage", sink)
    return rows


def use(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> FakeClient:
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return client


async def test_chat_uses_fast_model_and_logs_usage(configured: pytest.MonkeyPatch) -> None:
    client = use(configured, FakeClient())
    messages: Any = [{"role": "user", "content": "hi"}]
    with capture_logs() as logs:
        response = await llm.chat(messages, purpose="route", max_completion_tokens=5)

    assert response is COMPLETION
    [call] = client.chat.completions.calls
    assert call["model"] == "fast-model"
    assert call["max_completion_tokens"] == 5
    [line] = [entry for entry in logs if entry["event"] == "llm_call_completed"]
    assert line["purpose"] == "route"
    assert line["model"] == "fast-model"
    assert (line["prompt_tokens"], line["completion_tokens"]) == (12, 3)
    assert isinstance(line["latency_ms"], int)


async def test_reasoning_tier_uses_reasoning_model(configured: pytest.MonkeyPatch) -> None:
    client = use(configured, FakeClient())
    await llm.chat([{"role": "user", "content": "plan"}], purpose="act", tier="reasoning")
    assert client.chat.completions.calls[0]["model"] == "reasoning-model"


async def test_failure_is_logged_without_details_and_reraised(
    configured: pytest.MonkeyPatch,
) -> None:
    use(configured, FakeClient(chat=FakeEndpoint(error=RateLimited("sk-test-not-real leaked?"))))
    with capture_logs() as logs, pytest.raises(RateLimited):
        await llm.chat([{"role": "user", "content": "hi"}], purpose="respond")

    [line] = [entry for entry in logs if entry["event"] == "llm_call_failed"]
    assert (line["error_type"], line["status_code"], line["purpose"]) == (
        "RateLimited",
        429,
        "respond",
    )
    assert "sk-test-not-real" not in repr(logs)


async def test_missing_model_name_fails_before_any_call(configured: pytest.MonkeyPatch) -> None:
    client = use(configured, FakeClient())
    configured.setenv("OPENAI_MODEL_FAST", "")
    get_settings.cache_clear()
    with pytest.raises(llm.LLMNotConfiguredError, match="OPENAI_MODEL_FAST"):
        await llm.chat([{"role": "user", "content": "hi"}], purpose="route")
    assert client.chat.completions.calls == []


def test_missing_key_fails_clearly(configured: pytest.MonkeyPatch) -> None:
    configured.setenv("OPENAI_API_KEY", "")
    _reset_caches()
    with pytest.raises(llm.LLMNotConfiguredError, match="OPENAI_API_KEY"):
        llm.get_client()


def test_client_reads_key_from_config(configured: pytest.MonkeyPatch) -> None:
    client = llm.get_client()
    assert client.api_key == "sk-test-not-real"
    assert client.max_retries == 0  # our tenacity loop retries instead


async def test_embed_requests_schema_dimensions(configured: pytest.MonkeyPatch) -> None:
    client = use(configured, FakeClient())
    with capture_logs() as logs:
        vectors = await llm.embed(["prefers deep work after 9pm"])

    assert vectors == [[0.1, 0.2, 0.3]]
    [call] = client.embeddings.calls
    assert (call["model"], call["dimensions"]) == ("embed-model", 1536)
    [line] = [entry for entry in logs if entry["event"] == "llm_call_completed"]
    assert (line["purpose"], line["prompt_tokens"]) == ("embed", 4)


# --- retries and model fallback (task 3.10) ------------------------------------------------

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("boom", response=httpx.Response(code, request=REQUEST), body=None)


class Scripted:
    """Chat endpoint whose outcome per model is a queue of exceptions/responses."""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script, self.calls = script, []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs["model"])
        outcome = self.script[kwargs["model"]].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fallbacks(configured: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    configured.setenv("OPENAI_MODEL_FALLBACKS", "backup-1, fast-model, backup-2")
    get_settings.cache_clear()
    configured.setattr(llm, "RETRY_WAIT", wait_none())
    return configured


def scripted(monkeypatch: pytest.MonkeyPatch, script: dict[str, list[Any]]) -> Scripted:
    endpoint = Scripted(script)
    use(monkeypatch, FakeClient(chat=endpoint))  # type: ignore[arg-type]
    return endpoint


def test_candidates_start_with_the_tier_model_without_repeats(
    fallbacks: pytest.MonkeyPatch,
) -> None:
    assert llm.candidates("fast") == ["fast-model", "backup-1", "backup-2"]
    assert llm.candidates("reasoning") == ["reasoning-model", "backup-1", "fast-model", "backup-2"]


async def test_transient_errors_are_retried_on_the_same_model(
    fallbacks: pytest.MonkeyPatch,
) -> None:
    endpoint = scripted(
        fallbacks,
        {
            "fast-model": [
                openai.APIConnectionError(request=REQUEST),
                status_error(openai.RateLimitError, 429),
                COMPLETION,
            ]
        },
    )
    assert await llm.chat([{"role": "user", "content": "hi"}], purpose="route") is COMPLETION
    assert endpoint.calls == ["fast-model"] * 3


async def test_after_three_failures_the_next_model_is_tried(fallbacks: pytest.MonkeyPatch) -> None:
    down = [status_error(openai.InternalServerError, 503) for _ in range(3)]
    endpoint = scripted(fallbacks, {"fast-model": down, "backup-1": [COMPLETION]})
    with capture_logs() as logs:
        await llm.chat([{"role": "user", "content": "hi"}], purpose="act")
    assert endpoint.calls == ["fast-model"] * 3 + ["backup-1"]
    [line] = [e for e in logs if e["event"] == "llm_fallback"]
    assert (line["from_model"], line["to_model"], line["error_type"]) == (
        "fast-model", "backup-1", "InternalServerError",
    )  # fmt: skip


async def test_an_unknown_model_is_skipped_without_retrying(fallbacks: pytest.MonkeyPatch) -> None:
    endpoint = scripted(
        fallbacks,
        {"fast-model": [status_error(openai.NotFoundError, 404)], "backup-1": [COMPLETION]},
    )
    await llm.chat([{"role": "user", "content": "hi"}], purpose="act")
    assert endpoint.calls == ["fast-model", "backup-1"]


async def test_a_bad_request_is_ours_so_it_is_not_retried_anywhere(
    fallbacks: pytest.MonkeyPatch,
) -> None:
    endpoint = scripted(fallbacks, {"fast-model": [status_error(openai.BadRequestError, 400)]})
    with pytest.raises(openai.BadRequestError):
        await llm.chat([{"role": "user", "content": "hi"}], purpose="act")
    assert endpoint.calls == ["fast-model"]


async def test_when_every_model_fails_the_last_error_is_raised(
    fallbacks: pytest.MonkeyPatch,
) -> None:
    gone = {m: [status_error(openai.NotFoundError, 404)] for m in ("fast-model", "backup-1")}
    endpoint = scripted(
        fallbacks, gone | {"backup-2": [status_error(openai.PermissionDeniedError, 403)]}
    )
    with pytest.raises(openai.PermissionDeniedError):
        await llm.chat([{"role": "user", "content": "hi"}], purpose="act")
    assert endpoint.calls == ["fast-model", "backup-1", "backup-2"]


async def test_embed_retries_but_never_switches_model(fallbacks: pytest.MonkeyPatch) -> None:
    class Flaky(FakeEndpoint):
        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise openai.APIConnectionError(request=REQUEST)
            return EMBEDDING

    client = use(fallbacks, FakeClient(emb=Flaky()))
    assert await llm.embed(["x"]) == [[0.1, 0.2, 0.3]]
    assert [c["model"] for c in client.embeddings.calls] == ["embed-model", "embed-model"]


# --- llm_usage rows and cost (task 3.6) ---------------------------------------------------


async def test_every_call_records_usage_with_trace_and_cost(
    configured: pytest.MonkeyPatch, usage: list[LLMUsageCreate]
) -> None:
    configured.setenv("OPENAI_PRICES", "fast-model=0.50/2.00")
    get_settings.cache_clear()
    use(configured, FakeClient())
    bind_trace("trace-abc")
    try:
        await llm.chat([{"role": "user", "content": "hi"}], purpose="route")
        await llm.embed(["x"])
    finally:
        clear_contextvars()
    chat_row, embed_row = usage
    assert (chat_row.trace_id, chat_row.model, chat_row.purpose) == (
        "trace-abc",
        "fast-model",
        "route",
    )
    assert (chat_row.prompt_tokens, chat_row.completion_tokens) == (12, 3)
    # 12 * 0.50/1M + 3 * 2.00/1M = 0.000012
    assert chat_row.est_cost_usd == Decimal("0.000012")
    assert (embed_row.purpose, embed_row.est_cost_usd) == ("embed", None)  # no price set


async def test_unknown_price_warns_once_per_model(
    configured: pytest.MonkeyPatch, usage: list[LLMUsageCreate]
) -> None:
    configured.setattr(llm, "_unpriced", set())
    use(configured, FakeClient())
    with capture_logs() as logs:
        for _ in range(2):
            await llm.chat([{"role": "user", "content": "hi"}], purpose="act")
    assert [e["model"] for e in logs if e["event"] == "llm_price_unknown"] == ["fast-model"]
    assert [row.trace_id for row in usage] == ["untraced", "untraced"]


async def test_a_usage_write_failure_never_fails_the_call(configured: pytest.MonkeyPatch) -> None:
    async def broken(row: LLMUsageCreate) -> None:
        raise ConnectionError("db down")

    configured.setattr(llm, "record_usage", broken)
    use(configured, FakeClient())
    with capture_logs() as logs:
        assert await llm.chat([{"role": "user", "content": "hi"}], purpose="act") is COMPLETION
    assert any(e["event"] == "llm_usage_not_recorded" for e in logs)


async def test_store_usage_commits_its_own_row(engine: AsyncEngine) -> None:
    row = LLMUsageCreate(trace_id="store-usage-test", model="m", purpose="act", prompt_tokens=1)
    await llm.store_usage(row)
    async with AsyncSession(engine) as session, session.begin():
        found = await session.scalar(
            sa.select(LLMUsage).where(LLMUsage.trace_id == "store-usage-test")
        )
        assert found is not None and found.prompt_tokens == 1
        await session.execute(sa.delete(LLMUsage).where(LLMUsage.trace_id == "store-usage-test"))


async def test_a_rejected_temperature_is_dropped_once(fallbacks: pytest.MonkeyPatch) -> None:
    rejected = openai.BadRequestError(
        "Unsupported value: 'temperature' does not support 0 with this model.",
        response=httpx.Response(400, request=REQUEST),
        body=None,
    )
    endpoint = Recording({"fast-model": [rejected, COMPLETION]})
    use(fallbacks, FakeClient(chat=endpoint))  # type: ignore[arg-type]
    with capture_logs() as logs:
        await llm.chat([{"role": "user", "content": "hi"}], purpose="route", temperature=0)
    assert ["temperature" in kwargs for kwargs in endpoint.kwargs] == [True, False]
    assert any(e["event"] == "llm_param_dropped" for e in logs)


class Recording(Scripted):
    def __init__(self, script: dict[str, list[Any]]) -> None:
        super().__init__(script)
        self.kwargs: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)
        return await super().create(**kwargs)
