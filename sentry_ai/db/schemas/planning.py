"""DTOs for one-off work: tasks, day plans and reminders (DATA_MODEL sections 2, 4, 5)."""

from datetime import date

from pydantic import AwareDatetime, Field, model_validator

from sentry_ai.db import enums as e
from sentry_ai.db.schemas._base import _DTO, _Input, _Update


# §2 tasks
class TaskCreate(_Input):
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = None
    priority: int = Field(default=2, ge=1, le=4)
    due_at: AwareDatetime | None = None
    estimate_min: int | None = Field(default=None, gt=0)
    project: str | None = None
    source: e.TaskSource = "chat"


class TaskUpdate(_Update):
    not_null = frozenset({"title", "priority"})

    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    due_at: AwareDatetime | None = None
    estimate_min: int | None = Field(default=None, gt=0)
    scheduled_start: AwareDatetime | None = None
    project: str | None = None


class TaskDTO(_DTO):
    id: int
    title: str
    notes: str | None
    status: e.TaskStatus
    priority: int
    due_at: AwareDatetime | None
    estimate_min: int | None
    scheduled_start: AwareDatetime | None
    project: str | None
    source: e.TaskSource
    completed_at: AwareDatetime | None
    rolled_over_count: int
    archived_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TaskMatch(_DTO):
    task: TaskDTO
    similarity: float


# §4 plans
class PlanBlockCreate(_Input):
    start_at: AwareDatetime
    end_at: AwareDatetime
    kind: e.BlockKind
    ref_id: int | None = None
    title: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ends_after_start(self) -> "PlanBlockCreate":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class PlanBlockUpdate(_Update):
    not_null = frozenset({"start_at", "end_at"})

    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    gcal_event_id: str | None = None
    outcome: e.BlockOutcome | None = None

    @model_validator(mode="after")
    def _ends_after_start(self) -> "PlanBlockUpdate":
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class PlanBlockDTO(_DTO):
    id: int
    plan_id: int
    start_at: AwareDatetime
    end_at: AwareDatetime
    kind: e.BlockKind
    ref_id: int | None
    title: str
    gcal_event_id: str | None
    outcome: e.BlockOutcome | None


class DayPlanDTO(_DTO):
    id: int
    local_date: date
    status: e.PlanStatus
    summary: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    blocks: list[PlanBlockDTO] = Field(default_factory=list)


# §5 reminders
class ReminderCreate(_Input):
    message: str = Field(min_length=1, max_length=1000)
    fire_at: AwareDatetime
    target_kind: e.ReminderTarget = "freeform"
    target_id: int | None = None

    @model_validator(mode="after")
    def _target_matches_kind(self) -> "ReminderCreate":
        if (self.target_kind == "freeform") != (self.target_id is None):
            raise ValueError("target_id is required for linked reminders and absent for freeform")
        return self


class ReminderUpdate(_Update):
    not_null = frozenset({"message", "fire_at", "status", "nudge_sent"})

    message: str | None = Field(default=None, min_length=1, max_length=1000)
    fire_at: AwareDatetime | None = None
    status: e.ReminderStatus | None = None
    nudge_sent: bool | None = None
    job_id: str | None = None
    telegram_message_id: int | None = None


class ReminderDTO(_DTO):
    id: int
    target_kind: e.ReminderTarget
    target_id: int | None
    message: str
    fire_at: AwareDatetime
    status: e.ReminderStatus
    nudge_sent: bool
    job_id: str | None
    telegram_message_id: int | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
