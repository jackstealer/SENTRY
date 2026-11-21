"""The only place an OpenAI client exists (CONVENTIONS section 3). Model names come from config.

Every chat call retries transient errors with exponential backoff, then falls back through
OPENAI_MODEL_FALLBACKS. Fallback happens per LLM call, never by re-running a tool loop, so a
model switch can never repeat a tool's side effects.
"""

import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from functools import lru_cache, partial
from typing import Any, Literal, TypeVar

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from structlog.contextvars import get_contextvars
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.stop import stop_base

from sentry_ai.config import get_settings
from sentry_ai.db.repositories import SystemRepo
from sentry_ai.db.schemas import LLMUsageCreate
from sentry_ai.db.session import get_sessionmaker
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
MAX_RETRY_AFTER = 30.0  # seconds; a longer Retry-After (e.g. a daily cap) is not worth waiting for


UsageSink = Callable[[LLMUsageCreate], Awaitable[None]]
_PER_MILLION = Decimal(1_000_000)
_unpriced: set[str] = set()


class LLMNotConfiguredError(RuntimeError):
    """An OpenAI key or model name is missing from .env."""


@lru_cache
def get_client() -> AsyncOpenAI:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise LLMNotConfiguredError("OPENAI_API_KEY is not set")
    # max_retries=0: retries are ours (tenacity below), so they never stack with the SDK's
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url or None,  # None = the SDK's default (OpenAI)
        timeout=30.0,
        max_retries=0,
    )


def model_for(tier: ModelTier) -> str:
    settings = get_settings()
    name = settings.openai_model_fast if tier == "fast" else settings.openai_model_reasoning
    if not name:
        raise LLMNotConfiguredError(f"OPENAI_MODEL_{tier.upper()} is not set")
    return name


def candidates(tier: ModelTier) -> list[str]:
    """The tier's model first, then OPENAI_MODEL_FALLBACKS in order, without repeats."""
    return list(dict.fromkeys([model_for(tier), *get_settings().openai_model_fallbacks]))


def retry_after(exc: BaseException | None) -> float | None:
    """Seconds from a rate-limited response's Retry-After header, if it sent one."""
    response = getattr(exc, "response", None)
    value = getattr(response, "headers", {}).get("retry-after")
    try:
        return None if value is None else max(float(value), 0.0)
    except ValueError:
        return None


def _wait(state: RetryCallState) -> float:
    after = retry_after(state.outcome.exception() if state.outcome else None)
    return after if after is not None else RETRY_WAIT(state)


class _StopIfWaitIsLong(stop_base):
    """Stop retrying this model when the server asks for a wait longer than MAX_RETRY_AFTER."""

    def __call__(self, retry_state: RetryCallState) -> bool:
        after = retry_after(retry_state.outcome.exception() if retry_state.outcome else None)
        return after is not None and after > MAX_RETRY_AFTER


async def _with_retries(call: Callable[[], Awaitable[T]]) -> T:
    retrying = AsyncRetrying(
        stop=stop_after_attempt(ATTEMPTS_PER_MODEL) | _StopIfWaitIsLong(),
        wait=_wait,  # the server's Retry-After when given (free tiers send it), else backoff
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
    try:
        return await _request(model, messages, purpose, params)
    except openai.BadRequestError as exc:
        # some reasoning models accept only their default temperature: drop it, once
        if "temperature" not in params or "temperature" not in str(exc).lower():
            raise
        log.info("llm_param_dropped", purpose=purpose, model=model, param="temperature")
        rest = {k: v for k, v in params.items() if k != "temperature"}
        return await _request(model, messages, purpose, rest)


async def _request(
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
    await _completed(
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
        await _completed(
            "embed", model, started, prompt_tokens=response.usage.prompt_tokens, completion_tokens=0
        )
        return [item.embedding for item in response.data]

    return await _with_retries(once)


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def estimate_cost(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> Decimal | None:
    """USD from OPENAI_PRICES; None (and one warning per model) when the price is unknown."""
    price = get_settings().openai_prices.get(model)
    if price is None:
        if model not in _unpriced:
            _unpriced.add(model)
            log.warning("llm_price_unknown", model=model)
        return None
    cost = (price[0] * (prompt_tokens or 0) + price[1] * (completion_tokens or 0)) / _PER_MILLION
    return cost.quantize(Decimal("0.000001"))


async def store_usage(row: LLMUsageCreate) -> None:
    """Own short transaction: usage is kept even when the turn that spent it rolls back."""
    async with get_sessionmaker().begin() as session:
        await SystemRepo(session, get_settings().user_id).record_llm_usage(row)


record_usage: UsageSink = store_usage  # tests swap this for an in-memory list


async def _completed(
    purpose: str,
    model: str,
    started: float,
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    latency_ms = _latency_ms(started)
    cost = estimate_cost(model, prompt_tokens, completion_tokens)
    log.info(
        "llm_call_completed",
        purpose=purpose,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        est_cost_usd=None if cost is None else float(cost),
    )
    row = LLMUsageCreate(
        trace_id=str(get_contextvars().get("trace_id") or "untraced"),
        model=model,
        purpose=purpose,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        est_cost_usd=cost,
    )
    try:
        await record_usage(row)
    except Exception as exc:  # bookkeeping must never fail the user's turn
        log.warning("llm_usage_not_recorded", error_type=type(exc).__name__)


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
