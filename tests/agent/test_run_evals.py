"""scripts/run_evals.py plumbing, offline: the fake LLM stands in for the real API."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from sentry_ai.agent import llm
from sentry_ai.agent.evals import load_cases

from .conftest import FakeLLM, fake_embed

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_evals.py"
CASES = {c.id: c for c in load_cases()}


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(llm, "embed", fake_embed)  # never the real API from pytest
    spec = importlib.util.spec_from_file_location("run_evals", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_passing_case(runner: ModuleType, fake_llm: FakeLLM, engine: AsyncEngine) -> None:
    fake_llm.route("capture").call(("create_task", {"title": "Buy groceries"})).say("Added.")
    run = await runner.run_case(CASES["capture-undated-task"], engine)
    assert run.error is None and run.intent_ok and run.tools_ok
    assert run.turns[0].observed.calls == [("create_task", {"title": "Buy groceries"})]


async def test_a_confirmed_destructive_call_counts_for_the_yes_turn(
    runner: ModuleType, fake_llm: FakeLLM, engine: AsyncEngine
) -> None:
    fake_llm.route("manage").call(("archive_tasks", {"completed_before": "2026-11-23"}))
    fake_llm.route("manage", "yes")
    run = await runner.run_case(CASES["manage-archive-done-confirmed"], engine)
    asked, confirmed = run.turns
    assert asked.observed.awaiting_confirmation and asked.score.tools_ok
    assert confirmed.observed.calls == [("archive_tasks", {"completed_before": "2026-11-23"})]
    assert run.intent_ok and run.tools_ok


async def test_a_failing_case_is_reported(
    runner: ModuleType, fake_llm: FakeLLM, engine: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_llm.route("query").call(("create_task", {"title": "groceries"})).say("Done.")
    run = await runner.run_case(CASES["capture-undated-task"], engine)
    assert not run.intent_ok and run.tools_ok  # right tool, wrong intent
    assert runner.report([run], verbose=False) is False
    assert "FAIL  capture-undated-task" in capsys.readouterr().out
