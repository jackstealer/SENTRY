from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from sentry_ai.db.models import Base

PHASE_1_TABLES = {
    "routines",
    "tasks",
    "habits",
    "habit_logs",
    "day_plans",
    "plan_blocks",
    "reminders",
    "calendar_events",
    "episodic_log",
    "memories",
    "preferences",
    "proactive_runs",
    "llm_usage",
}


def ddl(name: str) -> str:
    table = Base.metadata.tables[name]
    parts = [str(CreateTable(table).compile(dialect=postgresql.dialect()))]
    parts += [str(CreateIndex(ix).compile(dialect=postgresql.dialect())) for ix in table.indexes]
    return "\n".join(parts)


def test_exactly_the_phase_1_tables() -> None:
    assert set(Base.metadata.tables) == PHASE_1_TABLES


def test_user_owned_tables_have_user_id() -> None:
    # plan_blocks inherit ownership via day_plans; proactive_runs/llm_usage are process-wide
    exempt = {"plan_blocks", "proactive_runs", "llm_usage"}
    for name in PHASE_1_TABLES - exempt:
        column = Base.metadata.tables[name].c["user_id"]
        assert not column.nullable, name


def test_timestamps_are_timestamptz() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.name.endswith("_at"):
                assert column.type.timezone, f"{table.name}.{column.name}"  # type: ignore[attr-defined]


def test_key_constraints_render() -> None:
    assert "CONSTRAINT ck_tasks_status CHECK (status IN ('todo', 'in_progress'" in ddl("tasks")
    assert "CHECK (priority BETWEEN 1 AND 4)" in ddl("tasks")
    assert "CHECK (end_at > start_at)" in ddl("plan_blocks")
    assert "ON DELETE CASCADE" in ddl("plan_blocks")
    plans = ddl("day_plans")
    assert "CREATE UNIQUE INDEX uq_day_plans_confirmed" in plans
    assert "WHERE status = 'confirmed'" in plans
    memories = ddl("memories")
    assert "embedding VECTOR(1536) NOT NULL" in memories
    assert "USING hnsw (embedding vector_cosine_ops)" in memories
    assert "metadata JSONB DEFAULT '{}'::jsonb NOT NULL" in ddl("episodic_log")
    assert "UNIQUE (habit_id, local_date)" in ddl("habit_logs")
    assert "PRIMARY KEY (job_key, local_date)" in ddl("proactive_runs")
