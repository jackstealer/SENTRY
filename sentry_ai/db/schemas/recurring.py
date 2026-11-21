"""DTOs for recurring things: routines (DATA_MODEL section 1) and habits (section 3)."""

from datetime import date, time

from pydantic import AwareDatetime, Field, field_validator, model_validator

from sentry_ai.db import enums as e
from sentry_ai.db.schemas._base import _DTO, _Input, _Update, validate_rrule


# §1 routines
class RoutineCreate(_Input):
    title: str = Field(min_length=1, max_length=200)
    category: e.RoutineCategory
    rrule: str
    start_time: time
    duration_min: int = Field(gt=0, le=24 * 60)
    is_fixed: bool = True
    remind_before_min: int | None = Field(default=None, ge=0)
    active_from: date | None = None
    active_until: date | None = None

    _rrule = field_validator("rrule")(validate_rrule)

    @model_validator(mode="after")
    def _window_order(self) -> "RoutineCreate":
        if self.active_from and self.active_until and self.active_until < self.active_from:
            raise ValueError("active_until is before active_from")
        return self


class RoutineUpdate(_Update):
    not_null = frozenset({"title", "category", "rrule", "start_time", "duration_min", "is_fixed"})

    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: e.RoutineCategory | None = None
    rrule: str | None = None
    start_time: time | None = None
    duration_min: int | None = Field(default=None, gt=0, le=24 * 60)
    is_fixed: bool | None = None
    remind_before_min: int | None = Field(default=None, ge=0)
    active_from: date | None = None
    active_until: date | None = None

    @field_validator("rrule")
    @classmethod
    def _rrule(cls, value: str | None) -> str | None:
        return None if value is None else validate_rrule(value)


class RoutineDTO(_DTO):
    id: int
    title: str
    category: e.RoutineCategory
    rrule: str
    start_time: time
    duration_min: int
    is_fixed: bool
    remind_before_min: int | None
    active_from: date | None
    active_until: date | None
    archived_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


# §3 habits
class HabitCreate(_Input):
    name: str = Field(min_length=1, max_length=200)
    rrule: str
    target_per_period: int = Field(default=1, ge=1)
    period: e.HabitPeriod = "day"
    preferred_time: time | None = None
    remind_before_min: int | None = Field(default=None, ge=0)

    _rrule = field_validator("rrule")(validate_rrule)

    @model_validator(mode="after")
    def _weekly_target_fits(self) -> "HabitCreate":
        if self.period == "week" and self.target_per_period > 7:
            raise ValueError("a weekly habit can be done at most 7 times a week")
        return self


class HabitUpdate(_Update):
    not_null = frozenset({"name", "rrule", "target_per_period", "period"})

    name: str | None = Field(default=None, min_length=1, max_length=200)
    rrule: str | None = None
    target_per_period: int | None = Field(default=None, ge=1)
    period: e.HabitPeriod | None = None
    preferred_time: time | None = None
    remind_before_min: int | None = Field(default=None, ge=0)

    @field_validator("rrule")
    @classmethod
    def _rrule(cls, value: str | None) -> str | None:
        return None if value is None else validate_rrule(value)


class HabitDTO(_DTO):
    id: int
    name: str
    rrule: str
    target_per_period: int
    period: e.HabitPeriod
    preferred_time: time | None
    remind_before_min: int | None
    archived_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class HabitLogDTO(_DTO):
    id: int
    habit_id: int
    local_date: date
    status: e.HabitLogStatus
    note: str | None
    logged_at: AwareDatetime
