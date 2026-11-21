"""Core schema: DATA_MODEL.md sections 1-8 (13 tables, indexes, CHECKs).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 20:17:59.786811
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("gcal_event_id", sa.Text(), nullable=False),
        sa.Column("calendar_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("all_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_sentry_owned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        sa.UniqueConstraint(
            "user_id",
            "calendar_id",
            "gcal_event_id",
            name=op.f("uq_calendar_events_user_id_calendar_id_gcal_event_id"),
        ),
    )
    op.create_table(
        "day_plans",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'confirmed', 'superseded')", name=op.f("ck_day_plans_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_day_plans")),
    )
    op.create_index(
        "uq_day_plans_confirmed",
        "day_plans",
        ["user_id", "local_date"],
        unique=True,
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.create_table(
        "episodic_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'tool', 'system_event')",
            name=op.f("ck_episodic_log_role"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_episodic_log")),
    )
    op.create_index(
        op.f("ix_episodic_log_user_id_created_at"),
        "episodic_log",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "habits",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("target_per_period", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("period", sa.Text(), server_default=sa.text("'day'"), nullable=False),
        sa.Column("preferred_time", sa.Time(), nullable=True),
        sa.Column("remind_before_min", sa.Integer(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("period IN ('day', 'week')", name=op.f("ck_habits_period")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_habits")),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("est_cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage")),
    )
    op.create_table(
        "memories",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("confidence", sa.REAL(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'preference', 'pattern', 'goal')", name=op.f("ck_memories_kind")
        ),
        sa.CheckConstraint(
            "source IN ('explicit', 'consolidation')", name=op.f("ck_memories_source")
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["memories.id"], name=op.f("fk_memories_superseded_by_memories")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "ix_memories_embedding_hnsw",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "preferences",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_preferences")),
    )
    op.create_table(
        "proactive_runs",
        sa.Column("job_key", sa.Text(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("job_key", "local_date", name=op.f("pk_proactive_runs")),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'scheduled'"), nullable=False),
        sa.Column("nudge_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("job_id", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'sent', 'acked', 'snoozed', "
            "'skipped', 'cancelled', 'expired')",
            name=op.f("ck_reminders_status"),
        ),
        sa.CheckConstraint(
            "target_kind IN ('task', 'habit', 'routine', 'plan_block', 'freeform')",
            name=op.f("ck_reminders_target_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index(
        op.f("ix_reminders_user_id_status_fire_at"),
        "reminders",
        ["user_id", "status", "fire_at"],
        unique=False,
    )
    op.create_table(
        "routines",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("is_fixed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("remind_before_min", sa.Integer(), nullable=True),
        sa.Column("active_from", sa.Date(), nullable=True),
        sa.Column("active_until", sa.Date(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("duration_min > 0", name=op.f("ck_routines_duration_min_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routines")),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'todo'"), nullable=False),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("2"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimate_min", sa.Integer(), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("project", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_over_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'skipped', 'cancelled')",
            name=op.f("ck_tasks_status"),
        ),
        sa.CheckConstraint("priority BETWEEN 1 AND 4", name=op.f("ck_tasks_priority_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(
        op.f("ix_tasks_user_id_status_due_at"),
        "tasks",
        ["user_id", "status", "due_at"],
        unique=False,
    )
    op.create_table(
        "habit_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("habit_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "logged_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('done', 'skipped', 'missed')", name=op.f("ck_habit_logs_status")
        ),
        sa.ForeignKeyConstraint(
            ["habit_id"], ["habits.id"], name=op.f("fk_habit_logs_habit_id_habits")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_habit_logs")),
        sa.UniqueConstraint(
            "habit_id", "local_date", name=op.f("uq_habit_logs_habit_id_local_date")
        ),
    )
    op.create_table(
        "plan_blocks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("gcal_event_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('routine', 'task', 'habit', 'calendar_event', 'break', 'buffer')",
            name=op.f("ck_plan_blocks_kind"),
        ),
        sa.CheckConstraint(
            "outcome IN ('done', 'partial', 'skipped', 'moved')",
            name=op.f("ck_plan_blocks_outcome"),
        ),
        sa.CheckConstraint("end_at > start_at", name=op.f("ck_plan_blocks_end_after_start")),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["day_plans.id"],
            name=op.f("fk_plan_blocks_plan_id_day_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_blocks")),
    )


def downgrade() -> None:
    op.drop_table("plan_blocks")
    op.drop_table("habit_logs")
    op.drop_index(op.f("ix_tasks_user_id_status_due_at"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("routines")
    op.drop_index(op.f("ix_reminders_user_id_status_fire_at"), table_name="reminders")
    op.drop_table("reminders")
    op.drop_table("proactive_runs")
    op.drop_table("preferences")
    op.drop_index(
        "ix_memories_embedding_hnsw",
        table_name="memories",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("memories")
    op.drop_table("llm_usage")
    op.drop_table("habits")
    op.drop_index(op.f("ix_episodic_log_user_id_created_at"), table_name="episodic_log")
    op.drop_table("episodic_log")
    op.drop_index(
        "uq_day_plans_confirmed",
        table_name="day_plans",
        postgresql_where=sa.text("status = 'confirmed'"),
    )
    op.drop_table("day_plans")
    op.drop_table("calendar_events")
