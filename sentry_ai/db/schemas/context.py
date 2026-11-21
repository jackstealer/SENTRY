"""DTOs for context and system data: calendar cache, memory, preferences, LLM usage (6-8)."""

from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, AwareDatetime, Field

from sentry_ai.db import enums as e
from sentry_ai.db.models import EMBEDDING_DIMS
from sentry_ai.db.schemas._base import _DTO, _Input


# §6 calendar cache
class CalendarEventUpsert(_Input):
    gcal_event_id: str
    calendar_id: str
    title: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    all_day: bool = False
    is_sentry_owned: bool = False
    synced_at: AwareDatetime


class CalendarEventDTO(_DTO):
    id: int
    gcal_event_id: str
    calendar_id: str
    title: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    all_day: bool
    is_sentry_owned: bool
    synced_at: AwareDatetime


# §7 memory + preferences
class EpisodeDTO(_DTO):
    id: int
    trace_id: str
    role: e.EpisodeRole
    content: str
    metadata: dict[str, Any] = Field(validation_alias=AliasChoices("meta", "metadata"))
    created_at: AwareDatetime


class MemoryCreate(_Input):
    content: str = Field(min_length=1)
    kind: e.MemoryKind
    embedding: list[float] = Field(min_length=EMBEDDING_DIMS, max_length=EMBEDDING_DIMS)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: e.MemorySource


class MemoryDTO(_DTO):
    id: int
    content: str
    kind: e.MemoryKind
    confidence: float
    source: e.MemorySource
    last_used_at: AwareDatetime | None
    superseded_by: int | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class MemoryMatch(_DTO):
    memory: MemoryDTO
    distance: float  # cosine distance: 0 = same direction, 2 = opposite


class PreferenceDTO(_DTO):
    key: str
    value: dict[str, Any]
    updated_at: AwareDatetime


# §8 system
class LLMUsageCreate(_Input):
    trace_id: str
    model: str
    purpose: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    est_cost_usd: Decimal | None = Field(default=None, ge=0)


class LLMUsageDTO(_DTO):
    id: int
    trace_id: str
    model: str
    purpose: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    est_cost_usd: Decimal | None
    created_at: AwareDatetime
