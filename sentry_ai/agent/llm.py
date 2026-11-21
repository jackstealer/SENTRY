"""The only place an OpenAI client exists (CONVENTIONS section 3). Model names come from config."""

import time
from functools import lru_cache
from typing import Any, Literal

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from sentry_ai.config import get_settings
from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)

ModelTier = Literal["fast", "reasoning"]


class LLMNotConfiguredError(RuntimeError):
    """An OpenAI key or model name is missing from .env."""


@lru_cache
def get_client() -> AsyncOpenAI:
    key = get_settings().openai_api_key
    if key is None:
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set")
    # shortcut: SDK retries (backoff, 2 attempts) only; the model fallback chain is task 3.10
    return AsyncOpenAI(api_key=key.get_secret_value(), timeout=30.0, max_retries=2)


def model_for(tier: ModelTier) -> str:
    settings = get_settings()
    name = settings.openai_model_fast if tier == "fast" else settings.openai_model_reasoning
    if not name:
        raise LLMNotConfiguredError(f"OPENAI_MODEL_{tier.upper()} is not set")
    return name


async def chat(
    messages: list[ChatCompletionMessageParam],
    *,
    purpose: str,
    tier: ModelTier = "fast",
    **params: Any,
) -> ChatCompletion:
    """One chat completion. `purpose` (route, act, respond, ...) tags the usage log line."""
    model = model_for(tier)
    started = time.perf_counter()
    try:
        # annotated: **params hides which SDK overload applies, so mypy would infer Any
        response: ChatCompletion = await get_client().chat.completions.create(
            model=model, messages=messages, stream=False, **params
        )
    except Exception as exc:
        _log_failed(purpose, model, started, exc)
        raise
    usage = response.usage
    _log_completed(
        purpose,
        model,
        started,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )
    return response


async def embed(texts: list[str]) -> list[list[float]]:
    """Embeddings for semantic memory, always at the dimension the schema's vector column uses."""
    settings = get_settings()
    model = settings.openai_embedding_model
    if not model:
        raise LLMNotConfiguredError("OPENAI_EMBEDDING_MODEL is not set")
    started = time.perf_counter()
    try:
        response = await get_client().embeddings.create(
            model=model, input=texts, dimensions=settings.openai_embedding_dims
        )
    except Exception as exc:
        _log_failed("embed", model, started, exc)
        raise
    _log_completed(
        "embed", model, started, prompt_tokens=response.usage.prompt_tokens, completion_tokens=0
    )
    return [item.embedding for item in response.data]


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _log_completed(
    purpose: str,
    model: str,
    started: float,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    log.info(
        "llm_call_completed",
        purpose=purpose,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=_latency_ms(started),
    )


def _log_failed(purpose: str, model: str, started: float, exc: Exception) -> None:
    # type + status only: SDK error messages can echo request details
    log.warning(
        "llm_call_failed",
        purpose=purpose,
        model=model,
        error_type=type(exc).__name__,
        status_code=getattr(exc, "status_code", None),
        latency_ms=_latency_ms(started),
    )
