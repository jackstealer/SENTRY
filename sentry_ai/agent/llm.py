"""The only place an OpenAI client exists (CONVENTIONS section 3). Model names come from config.

Every chat call retries transient errors with exponential backoff, then falls back through
OPENAI_MODEL_FALLBACKS. Fallback happens per LLM call, never by re-running a tool loop, so a
model switch can never repeat a tool's side effects.
"""

import time
from collections.abc import Awaitable, Callable
from functools import lru_cache, partial
from typing import Any, Literal, TypeVar

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from sentry_ai.config import get_settings
from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)

ModelTier = Literal["fast", "reasoning"]
T = TypeVar("T")

# may clear up on their own: retry the same model, then move on to the next one
TRANSIENT: tuple[type[Exception], ...] = (
    openai.APIConnectionError,  # includes timeouts
    openai.RateLimitError,
    openai.InternalServerError,
)
# this model is unusable for us (unknown name, no access): skip straight to the next one
MODEL_UNAVAILABLE: tuple[type[Exception], ...] = (
    openai.NotFoundError,
    openai.PermissionDeniedError,
)
ATTEMPTS_PER_MODEL = 3
RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=4)  # 1 s, 2 s (tests set wait_none)


class LLMNotConfiguredError(RuntimeError):
    """An OpenAI key or model name is missing from .env."""


@lru_cache
def get_client() -> AsyncOpenAI:
    key = get_settings().openai_api_key
    if key is None:
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set")
    # max_retries=0: retries are ours (tenacity below), so they never stack with the SDK's
    return AsyncOpenAI(api_key=key.get_secret_value(), timeout=30.0, max_retries=0)


def model_for(tier: ModelTier) -> str:
    settings = get_settings()
    name = settings.openai_model_fast if tier == "fast" else settings.openai_model_reasoning
    if not name:
        raise LLMNotConfiguredError(f"OPENAI_MODEL_{tier.upper()} is not set")
    return name


def candidates(tier: ModelTier) -> list[str]:
    """The tier's model first, then OPENAI_MODEL_FALLBACKS in order, without repeats."""
    return list(dict.fromkeys([model_for(tier), *get_settings().openai_model_fallbacks]))


async def _with_retries(call: Callable[[], Awaitable[T]]) -> T:
    retrying = AsyncRetrying(
        stop=stop_after_attempt(ATTEMPTS_PER_MODEL),
        wait=RETRY_WAIT,
        retry=retry_if_exception_type(TRANSIENT),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            return await call()
    raise AssertionError("unreachable: tenacity either returns or re-raises")


async def chat(
    messages: list[ChatCompletionMessageParam],
    *,
    purpose: str,
    tier: ModelTier = "fast",
    **params: Any,
) -> ChatCompletion:
    """One chat completion. `purpose` (route, act, respond, ...) tags the usage log line."""
    models = candidates(tier)
    for i, model in enumerate(models):
        try:
            return await _with_retries(partial(_chat_once, model, messages, purpose, params))
        except (*TRANSIENT, *MODEL_UNAVAILABLE) as exc:
            if i + 1 == len(models):
                raise
            log.warning(
                "llm_fallback",
                purpose=purpose,
                from_model=model,
                to_model=models[i + 1],
                error_type=type(exc).__name__,
            )
    raise AssertionError("unreachable: the last model either returns or raises")


async def _chat_once(
    model: str, messages: list[ChatCompletionMessageParam], purpose: str, params: dict[str, Any]
) -> ChatCompletion:
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
    """Embeddings for semantic memory, always at the dimension the schema's vector column uses.

    Retries only: a fallback embedding model would produce vectors that do not compare.
    """
    settings = get_settings()
    model = settings.openai_embedding_model
    if not model:
        raise LLMNotConfiguredError("OPENAI_EMBEDDING_MODEL is not set")

    async def once() -> list[list[float]]:
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

    return await _with_retries(once)


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
