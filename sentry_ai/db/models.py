"""ORM models for DATA_MODEL.md sections 1-8. Section 10-11 tables arrive with their phases."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, get_args

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    REAL,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from sentry_ai.db import enums

EMBEDDING_DIMS = 1536  # = OPENAI_EMBEDDING_DIMS; changing it needs a migration and a re-embed

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        datetime: DateTime(timezone=True),
        date: Date,
        time: Time,
        str: Text,
        dict[str, Any]: JSONB,
    }


def _check_in(column: str, allowed: Any) -> CheckConstraint:
    values = ", ".join(f"'{value}'" for value in get_args(allowed))
    return CheckConstraint(f"{column} IN ({values})", name=column)


class _Created:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class _Timestamps(_Created):
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# §1
class Routine(_Timestamps, Base):
    __tablename__ = "routines"
    __table_args__ = (CheckConstraint("duration_min > 0", name="duration_min_positive"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    title: Mapped[str]
    category: Mapped[str]
    rrule: Mapped[str]
    start_time: Mapped[time]  # local wall time in USER_TZ
    duration_min: Mapped[int]
    is_fixed: Mapped[bool] = mapped_column(server_default=text("true"))
    remind_before_min: Mapped[int | None]
    active_from: Mapped[date | None]
    active_until: Mapped[date | None]
    archived_at: Mapped[datetime | None]


# §2
class Task(_Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        _check_in("status", enums.TaskStatus),
        CheckConstraint("priority BETWEEN 1 AND 4", name="priority_range"),
        Index(None, "user_id", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    title: Mapped[str]
    notes: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'todo'"))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("2"))
    due_at: Mapped[datetime | None]
    estimate_min: Mapped[int | None]
    scheduled_start: Mapped[datetime | None]
    project: Mapped[str | None]
    source: Mapped[str] = mapped_column(server_default=text("'chat'"))
    completed_at: Mapped[datetime | None]
    rolled_over_count: Mapped[int] = mapped_column(server_default=text("0"))
    archived_at: Mapped[datetime | None]


# §3
class Habit(_Timestamps, Base):
    __tablename__ = "habits"
    __table_args__ = (_check_in("period", enums.HabitPeriod),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    name: Mapped[str]
    rrule: Mapped[str]
    target_per_period: Mapped[int] = mapped_column(server_default=text("1"))
    period: Mapped[str] = mapped_column(server_default=text("'day'"))
    preferred_time: Mapped[time | None]
    remind_before_min: Mapped[int | None]
    archived_at: Mapped[datetime | None]


class HabitLog(Base):
    __tablename__ = "habit_logs"
    __table_args__ = (
        _check_in("status", enums.HabitLogStatus),
        UniqueConstraint("habit_id", "local_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    habit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("habits.id"))
    local_date: Mapped[date]
    status: Mapped[str]
    note: Mapped[str | None]
    logged_at: Mapped[datetime] = mapped_column(server_default=func.now())


# §4
class DayPlan(_Timestamps, Base):
    __tablename__ = "day_plans"
    __table_args__ = (
        _check_in("status", enums.PlanStatus),
        Index(
            "uq_day_plans_confirmed",
            "user_id",
            "local_date",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    local_date: Mapped[date]
    status: Mapped[str] = mapped_column(server_default=text("'draft'"))
    summary: Mapped[str | None]


class PlanBlock(Base):
    __tablename__ = "plan_blocks"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="end_after_start"),
        _check_in("kind", enums.BlockKind),
        _check_in("outcome", enums.BlockOutcome),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("day_plans.id", ondelete="CASCADE"))
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    kind: Mapped[str]
    ref_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str]
    gcal_event_id: Mapped[str | None]
    outcome: Mapped[str | None]


# §5
class Reminder(_Timestamps, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        _check_in("target_kind", enums.ReminderTarget),
        _check_in("status", enums.ReminderStatus),
        Index(None, "user_id", "status", "fire_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    target_kind: Mapped[str]
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    message: Mapped[str]
    fire_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default=text("'scheduled'"))
    nudge_sent: Mapped[bool] = mapped_column(server_default=text("false"))
    job_id: Mapped[str | None]
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)


# §6
class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "calendar_id", "gcal_event_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    gcal_event_id: Mapped[str]
    calendar_id: Mapped[str]
    title: Mapped[str]
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    all_day: Mapped[bool] = mapped_column(server_default=text("false"))
    is_sentry_owned: Mapped[bool] = mapped_column(server_default=text("false"))
    synced_at: Mapped[datetime]


# §7
class EpisodicEntry(_Created, Base):
    __tablename__ = "episodic_log"
    __table_args__ = (
        _check_in("role", enums.EpisodeRole),
        Index(None, "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    trace_id: Mapped[str]
    role: Mapped[str]
    content: Mapped[str]
    # attribute is `meta`: `metadata` is reserved on declarative classes
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", server_default=text("'{}'::jsonb"))


class Memory(_Timestamps, Base):
    __tablename__ = "memories"
    __table_args__ = (
        _check_in("kind", enums.MemoryKind),
        _check_in("source", enums.MemorySource),
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str]
    content: Mapped[str]
    kind: Mapped[str]
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMS))
    confidence: Mapped[float] = mapped_column(REAL, server_default=text("1.0"))
    source: Mapped[str]
    last_used_at: Mapped[datetime | None]
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("memories.id"))


class Preference(Base):
    __tablename__ = "preferences"

    user_id: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


# §8
class ProactiveRun(Base):
    __tablename__ = "proactive_runs"

    job_key: Mapped[str] = mapped_column(primary_key=True)
    local_date: Mapped[date] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LLMUsage(_Created, Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trace_id: Mapped[str]
    model: Mapped[str]
    purpose: Mapped[str]
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    latency_ms: Mapped[int | None]
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
