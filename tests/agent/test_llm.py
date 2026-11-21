from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion
from structlog.testing import capture_logs

from sentry_ai.agent import llm
from sentry_ai.config import get_settings

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
    assert client.max_retries == 2


async def test_embed_requests_schema_dimensions(configured: pytest.MonkeyPatch) -> None:
    client = use(configured, FakeClient())
    with capture_logs() as logs:
        vectors = await llm.embed(["prefers deep work after 9pm"])

    assert vectors == [[0.1, 0.2, 0.3]]
    [call] = client.embeddings.calls
    assert (call["model"], call["dimensions"]) == ("embed-model", 1536)
    [line] = [entry for entry in logs if entry["event"] == "llm_call_completed"]
    assert (line["purpose"], line["prompt_tokens"]) == ("embed", 4)
