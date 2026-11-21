"""Shared DTO bases and the RRULE validator."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from dateutil.rrule import rrulestr
from pydantic import BaseModel, ConfigDict, model_validator

_RRULE_PROBE_START = datetime(2026, 1, 1, tzinfo=UTC)
_FORBIDDEN_RRULE_PARTS = ("DTSTART", "RDATE", "EXDATE", "\n", "\r")


def validate_rrule(rule: str) -> str:
    """An RRULE body only (e.g. FREQ=WEEKLY;BYDAY=MO); the start date comes from the row."""
    cleaned = rule.strip().upper().removeprefix("RRULE:")
    if not cleaned or any(part in cleaned for part in _FORBIDDEN_RRULE_PARTS):
        raise ValueError("rrule must be one RRULE body without DTSTART, e.g. FREQ=DAILY")
    try:
        rrulestr(cleaned, dtstart=_RRULE_PROBE_START)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid rrule {rule!r}: {exc}") from exc
    return cleaned


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Update(_Input):
    """Partial update: only fields explicitly set are written (`model_dump(exclude_unset=True)`)."""

    not_null: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_null_for_required_columns(self) -> "_Update":
        nulled = sorted(
            f for f in self.model_fields_set & self.not_null if getattr(self, f) is None
        )
        if nulled:
            raise ValueError(f"cannot set {', '.join(nulled)} to null")
        return self

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class _DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)
