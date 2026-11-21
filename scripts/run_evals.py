"""Run the agent eval set against the REAL OpenAI API and print pass rates (ROADMAP 3.9).

Usage:
    uv run python scripts/run_evals.py                  # all cases
    uv run python scripts/run_evals.py --only capture   # ids containing "capture"
    uv run python scripts/run_evals.py -v               # also print each reply

Needs OPENAI_API_KEY and the model names in .env (OPENAI_PRICES for costs). Each case runs on
the `<db>_test` database inside a transaction that is always rolled back, with a fresh
conversation and the case's frozen clock. Not part of `pytest`: it costs money.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from structlog.testing import capture_logs

from sentry_ai.agent import llm
from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.evals import (
    EvalCase,
    Observed,
    TurnExpect,
    TurnScore,
    build_setup,
    calls_in,
    load_cases,
    score_turn,
)
from sentry_ai.agent.graph import Graph, build_graph, run_turn, thread_id
from sentry_ai.config import Settings, get_settings
from sentry_ai.db.models import Habit
from sentry_ai.db.repositories import (
    HabitRepo,
    MemoryRepo,
    PreferenceRepo,
    ReminderRepo,
    RoutineRepo,
    TaskRepo,
)
from sentry_ai.db.schemas import LLMUsageCreate, MemoryCreate
from sentry_ai.db.session import get_engine
from sentry_ai.services.time import local_date_of

ROOT = Path(__file__).resolve().parents[1]
EVAL_USER = "eval-user"
INTENT_TARGET, TOOLS_TARGET, SIMPLE_COST_TARGET = 0.90, 0.85, 0.005
SIMPLE_INTENTS = {"capture", "checkin", "query", "smalltalk"}


@dataclass
class TurnRun:
    say: str
    score: TurnScore
    observed: Observed
    cost: float | None
    seconds: float


@dataclass
class CaseRun:
    case: EvalCase
    turns: list[TurnRun] = field(default_factory=list)
    error: str | None = None

    @property
    def intent_ok(self) -> bool:
        return self.error is None and all(t.score.intent_ok for t in self.turns)

    @property
    def tools_ok(self) -> bool:
        return self.error is None and all(t.score.tools_ok for t in self.turns)


def use_test_database() -> None:
    """Point everything at `<db>_test`, exactly like the pytest suite does."""
    url = make_url(os.environ.get("DATABASE_URL") or Settings().database_url)
    if not (url.database or "").endswith("_test"):
        url = url.set(database=f"{url.database}_test")
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    get_engine.cache_clear()


def migrate() -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("%", "%%"))
    command.upgrade(cfg, "head")


async def seed(case: EvalCase, session: AsyncSession) -> dict[str, int]:
    """Create the case's setup; returns refs like {"tasks.0": 12} for the matchers."""
    items, refs = build_setup(case), {}
    for key, value in items.prefs.items():
        await PreferenceRepo(session, EVAL_USER).set(key, value)
    tasks = TaskRepo(session, EVAL_USER)
    for i, task in enumerate(items.tasks):
        refs[f"tasks.{i}"] = (await tasks.create(task)).id
    for i, (task, completed_at) in enumerate(items.done_tasks):
        created = await tasks.create(task)
        await tasks.set_status(created.id, "done", completed_at)
        refs[f"done_tasks.{i}"] = created.id
    habits = HabitRepo(session, EVAL_USER)
    for i, habit in enumerate(items.habits):
        created_habit = await habits.create(habit)
        # habits start on their creation day: backdate so the case's day is a due day
        await session.execute(
            sa.update(Habit)
            .where(Habit.id == created_habit.id)
            .values(created_at=case.now - timedelta(days=30))
        )
        refs[f"habits.{i}"] = created_habit.id
    for i, routine in enumerate(items.routines):
        refs[f"routines.{i}"] = (await RoutineRepo(session, EVAL_USER).create(routine)).id
    for i, reminder in enumerate(items.reminders):
        refs[f"reminders.{i}"] = (await ReminderRepo(session, EVAL_USER).create(reminder)).id
    if items.memories:
        vectors = await llm.embed([content for content, _ in items.memories])
        for (content, kind), vector in zip(items.memories, vectors, strict=True):
            await MemoryRepo(session, EVAL_USER).add(
                MemoryCreate(content=content, kind=kind, embedding=vector, source="explicit")
            )
    return refs


async def run_case(case: EvalCase, engine: AsyncEngine) -> CaseRun:
    run = CaseRun(case)
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

        @asynccontextmanager
        async def sessions() -> AsyncIterator[AsyncSession]:
            async with session.begin_nested():
                yield session

        try:
            refs = await seed(case, session)
            # embed looked up now (not the dataclass default) so tests can swap it
            deps = AgentDeps(sessions=sessions, embed=llm.embed, clock=lambda: case.now)
            graph = build_graph(InMemorySaver())  # a fresh conversation per case
            for turn in case.turns:
                run.turns.append(await _run_turn(graph, deps, turn.say, turn.expect, refs))
        except Exception as exc:  # report the case as failed, keep going with the rest
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            await session.close()
            await outer.rollback()
    return run


async def _run_turn(
    graph: Graph, deps: AgentDeps, say: str, expect: TurnExpect, refs: dict[str, int]
) -> TurnRun:
    config: Any = {"configurable": {"thread_id": thread_id(EVAL_USER, local_date_of(deps.clock()))}}
    before = (await graph.aget_state(config)).values
    started = time.perf_counter()
    with capture_logs() as logs:
        result = await run_turn(graph, say, deps=deps, user_id=EVAL_USER)
    seconds = time.perf_counter() - started
    after = (await graph.aget_state(config)).values
    new_messages = after["messages"][len(before.get("messages", [])) :]
    executed = any(e["event"] == "confirmation_executed" for e in logs)
    observed = Observed(
        intent=result.intent,
        calls=calls_in(new_messages, before.get("pending_confirmation"), executed),
        awaiting_confirmation=result.awaiting_confirmation,
        reply=result.reply,
    )
    costs: list[float | None] = [
        e.get("est_cost_usd") for e in logs if e["event"] == "llm_call_completed"
    ]
    cost = None if any(c is None for c in costs) else sum(c or 0.0 for c in costs)
    return TurnRun(say, score_turn(expect, observed, refs), observed, cost, seconds)


def report(runs: list[CaseRun], verbose: bool) -> bool:
    for run in runs:
        status = "PASS" if run.intent_ok and run.tools_ok else "FAIL"
        cost = sum(t.cost or 0 for t in run.turns)
        secs = sum(t.seconds for t in run.turns)
        intents = ",".join(str(t.observed.intent) for t in run.turns)
        print(f"{status}  {run.case.id:34s} intent={intents:20s} ${cost:.4f}  {secs:4.1f}s")
        if run.error:
            print(f"      error: {run.error}")
        for i, t in enumerate(run.turns, 1):
            if not t.score.intent_ok:
                wanted = run.case.turns[i - 1].expect.intent
                print(f"      turn {i}: intent {t.observed.intent!r}, wanted {wanted}")
            for problem in t.score.problems:
                print(f"      turn {i}: {problem}")
            if verbose:
                print(f"      turn {i} reply: {t.observed.reply}")
    total = len(runs)
    intent_rate = sum(r.intent_ok for r in runs) / total
    tools_rate = sum(r.tools_ok for r in runs) / total
    turns = [t for r in runs for t in r.turns]
    simple = [t for t in turns if t.observed.intent in SIMPLE_INTENTS]
    priced = all(t.cost is not None for t in turns)
    print()
    passed_intent, passed_tools = sum(r.intent_ok for r in runs), sum(r.tools_ok for r in runs)
    print(
        f"Intent:      {passed_intent}/{total} = {intent_rate:.1%}  (target >= {INTENT_TARGET:.0%})"
    )
    print(f"Tools+args:  {passed_tools}/{total} = {tools_rate:.1%}  (target >= {TOOLS_TARGET:.0%})")
    ok = intent_rate >= INTENT_TARGET and tools_rate >= TOOLS_TARGET
    if priced and turns:
        avg = sum(t.cost or 0 for t in turns) / len(turns)
        avg_simple = sum(t.cost or 0 for t in simple) / max(len(simple), 1)
        print(
            f"Cost:        ${avg:.4f} per turn; simple turns ${avg_simple:.4f}"
            f"  (target < ${SIMPLE_COST_TARGET})"
        )
        ok = ok and avg_simple < SIMPLE_COST_TARGET
    else:
        print("Cost:        unknown for some models; set OPENAI_PRICES in .env")
    s = get_settings()
    print(f"Models:      fast={s.openai_model_fast} reasoning={s.openai_model_reasoning}")
    return ok


async def main(args: argparse.Namespace) -> int:
    use_test_database()
    await asyncio.to_thread(migrate)
    usage: list[LLMUsageCreate] = []

    async def keep(row: LLMUsageCreate) -> None:  # usage rows stay in memory, not in the DB
        usage.append(row)

    llm.record_usage = keep
    cases = [c for c in load_cases() if args.only in c.id]
    engine = get_engine()
    runs = []
    try:
        for case in cases:
            runs.append(await run_case(case, engine))
            print(".", end="", flush=True)
        print()
    finally:
        await engine.dispose()
    return 0 if report(runs, args.verbose) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="run case ids containing this text")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every reply")
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        sys.exit(runner.run(main(parser.parse_args())))
