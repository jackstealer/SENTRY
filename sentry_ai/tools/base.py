"""Tool building blocks (TOOLS_SPEC section 1). Tools are the only way the LLM changes anything."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent import llm
from sentry_ai.services.reminders import (
    NoopReminderScheduler,
    ReminderScheduler,
    ReminderService,
)
from sentry_ai.services.time import local_date_of, to_local

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


class ToolTier(StrEnum):
    READ = "read"  # no side effects
    WRITE = "write"  # creates/updates user data, reversible
    DESTRUCTIVE = "destructive"  # deletes data or bulk changes: needs the user's confirmation


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""  # one line the LLM uses to phrase the reply
    error: str | None = None


class ToolArgs(BaseModel):
    """Base for tool inputs: unknown keys are errors, so a misspelt argument is never ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user_id: str
    trace_id: str
    now: AwareDatetime  # converted to USER_TZ
    session: AsyncSession
    confirmed: bool = False  # True only when re-run after the user confirmed
    reminders: ReminderScheduler = Field(default_factory=NoopReminderScheduler)
    embed: Embedder = llm.embed

    @field_validator("now")
    @classmethod
    def _local(cls, value: datetime) -> datetime:
        return to_local(value)

    @property
    def today(self) -> date:
        return local_date_of(self.now)

    def reminder_service(self) -> ReminderService:
        return ReminderService(self.session, self.user_id, self.reminders, now=self.now)


Run = Callable[[Any, ToolContext], Awaitable[ToolResult]]
# a preview returns the confirmation summary, or a failed ToolResult when there is nothing to do
Preview = Callable[[Any, ToolContext], Awaitable["str | ToolResult"]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # written for the LLM: when to use it, when not to
    tier: ToolTier
    input_model: type[BaseModel]
    run: Run
    preview: Preview | None = None  # destructive only: "This will delete ..." for the confirmation
    category: str = ""  # the tools/<group>.py module it lives in


def tool(
    tier: ToolTier, args: type[BaseModel], *, preview: Preview | None = None
) -> Callable[[Run], Tool]:
    """Turn `async def name(args, ctx) -> ToolResult` into a Tool; docstring = description."""

    def wrap(fn: Run) -> Tool:
        if (tier is ToolTier.DESTRUCTIVE) != (preview is not None):
            raise TypeError(f"{fn.__name__}: destructive tools, and only they, need a preview")
        description = inspect.cleandoc(fn.__doc__ or "")
        if not description:
            raise TypeError(f"{fn.__name__}: the docstring is the LLM description and is required")
        return Tool(
            name=fn.__name__,
            description=description,
            tier=tier,
            input_model=args,
            run=fn,
            preview=preview,
            category=fn.__module__.rsplit(".", 1)[-1],
        )

    return wrap


def ok(message: str, **data: Any) -> ToolResult:
    return ToolResult(ok=True, message=message, data=to_data(data))


def fail(error: str, message: str, **data: Any) -> ToolResult:
    """An expected failure (not found, time in the past): the LLM reads `message` and recovers."""
    return ToolResult(ok=False, error=error, message=message, data=to_data(data))


def when(value: datetime) -> str:
    """Short local label for messages, e.g. "Fri 27 Nov 17:00"."""
    return to_local(value).strftime("%a %d %b %H:%M")


def to_data(value: Any) -> Any:
    """JSON-safe tool output; datetimes are shown in USER_TZ so the LLM never converts from UTC."""
    if isinstance(value, BaseModel):
        return to_data(value.model_dump())
    if isinstance(value, dict):
        return {str(k): to_data(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_data(v) for v in value]
    if isinstance(value, datetime):
        return to_local(value).isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, Decimal):
        return float(value)
    return value
