"""Known preference keys (DATA_MODEL section 7) and their value shapes; the planner relies on these.

Values are stored in canonical JSON form, e.g. wake_time -> {"time": "06:30"}. Short forms
from USER_PROFILE.md ("06:30", 15, 5) are accepted for single-field shapes.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TimeValue(_Value):
    time: str = Field(pattern=HHMM)


class WindowValue(_Value):
    """May wrap midnight (quiet hours 23:30-06:30)."""

    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)

    @model_validator(mode="after")
    def _not_empty(self) -> "WindowValue":
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class WeeklyValue(_Value):
    day: Literal["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    time: str = Field(pattern=HHMM)


class MinutesValue(_Value):
    minutes: int = Field(ge=0, le=24 * 60)


class CountValue(_Value):
    count: int = Field(ge=1, le=50)


PREFERENCE_SCHEMAS: dict[str, type[_Value]] = {
    "wake_time": TimeValue,
    "sleep_time": TimeValue,
    "evening_review_time": TimeValue,
    "weekly_review": WeeklyValue,
    "no_tasks_before": TimeValue,
    "deep_work_window": WindowValue,
    "default_reminder_min": MinutesValue,
    "quiet_hours": WindowValue,
    "max_tasks_per_day": CountValue,
}


class UnknownPreferenceError(ValueError):
    pass


def validate_preference(key: str, value: object) -> dict[str, Any]:
    """Canonical JSON value for a known key, or ValueError saying what is wrong."""
    schema = PREFERENCE_SCHEMAS.get(key)
    if schema is None:
        known = ", ".join(sorted(PREFERENCE_SCHEMAS))
        raise UnknownPreferenceError(f"unknown preference {key!r}; known keys: {known}")
    if not isinstance(value, dict):
        fields = list(schema.model_fields)
        if len(fields) != 1:
            raise ValueError(f"{key} needs an object with {', '.join(fields)}")
        value = {fields[0]: value}
    try:
        return schema.model_validate(value).model_dump()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        hint = ' (quote times in YAML: "18:00", not 18:00)' if "valid string" in problems else ""
        raise ValueError(f"{key}: {problems}{hint}") from exc
