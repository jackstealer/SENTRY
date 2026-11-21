"""Allowed values for TEXT columns: one source for the DB CHECK constraints and the DTO types."""

from typing import Literal

# CHECK-constrained in the DB (DATA_MODEL shows a CHECK for these)
TaskStatus = Literal["todo", "in_progress", "done", "skipped", "cancelled"]
HabitPeriod = Literal["day", "week"]
HabitLogStatus = Literal["done", "skipped", "missed"]
PlanStatus = Literal["draft", "confirmed", "superseded"]
BlockKind = Literal["routine", "task", "habit", "calendar_event", "break", "buffer"]
BlockOutcome = Literal["done", "partial", "skipped", "moved"]
ReminderTarget = Literal["task", "habit", "routine", "plan_block", "freeform"]
ReminderStatus = Literal["scheduled", "sent", "acked", "snoozed", "skipped", "cancelled", "expired"]
EpisodeRole = Literal["user", "assistant", "tool", "system_event"]
MemoryKind = Literal["fact", "preference", "pattern", "goal"]
MemorySource = Literal["explicit", "consolidation"]

# validated in DTOs only (DATA_MODEL lists them as comments, not CHECKs)
RoutineCategory = Literal["study", "fitness", "health", "personal", "work", "other"]
TaskSource = Literal["chat", "review_rollover", "calendar", "api"]
