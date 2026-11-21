"""sentry_ai/cli.py (kept next to the agent tests to share the fake LLM and deps fixtures)."""

from collections.abc import AsyncIterator

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from sentry_ai import cli
from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.graph import build_graph

from .conftest import FakeLLM


async def lines(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


async def test_repl_prints_each_reply_with_its_trace_and_stops_at_exit(
    fake_llm: FakeLLM, deps: AgentDeps
) -> None:
    fake_llm.route("smalltalk").say("Evening.").route("query").say("Nothing due today.")
    printed: list[str] = []
    await cli.converse(
        build_graph(InMemorySaver()),
        deps,
        lines("hi", "   ", "what's on today?", "exit", "never sent"),
        printed.append,
    )
    assert len(printed) == 2
    first, second = (p.splitlines() for p in printed)
    assert first[0] == "Evening." and first[1].startswith("[trace ") and len(first[1]) == 40
    assert second[0] == "Nothing due today."
    assert fake_llm.purposes() == ["route", "act", "route", "act"]


async def test_a_failing_turn_is_reported_and_the_repl_carries_on(
    fake_llm: FakeLLM, deps: AgentDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = cli.run_turn
    calls = {"n": 0}

    async def flaky(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("db down")
        return await real(*args, **kwargs)

    monkeypatch.setattr(cli, "run_turn", flaky)
    fake_llm.route("smalltalk").say("Back.")
    printed: list[str] = []
    await cli.converse(build_graph(InMemorySaver()), deps, lines("hi", "hi again"), printed.append)
    assert printed[0] == "error: ConnectionError: db down"
    assert printed[1].startswith("Back.")


def test_arguments_need_a_message_or_repl_but_not_both(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.parse_args(["what's", "on"]).message == ["what's", "on"]
    assert cli.parse_args(["--repl", "-vv"]).verbose == 2
    for bad in ([], ["--repl", "hi"]):
        with pytest.raises(SystemExit):
            cli.parse_args(bad)
    assert "give a message, or --repl" in capsys.readouterr().err
