"""Offline checks of the eval set and its scoring. The real-API run is scripts/run_evals.py."""

from collections import Counter

import pytest

from sentry_ai.agent.evals import (
    Call,
    EvalCase,
    Observed,
    ToolExpect,
    TurnExpect,
    arg_matches,
    build_setup,
    load_cases,
    same,
    score_turn,
)
from sentry_ai.tools.registry import INTENTS

CASES = load_cases()


def test_there_are_at_least_30_cases_covering_every_intent() -> None:
    assert len(CASES) >= 30
    first_intents: Counter[str] = Counter()
    for case in CASES:
        intent = case.turns[0].expect.intent
        first_intents.update([intent] if isinstance(intent, str) else intent[:1] if intent else [])
    assert set(first_intents) == set(INTENTS)
    assert min(first_intents.values()) >= 2


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_every_case_has_a_valid_setup(case: EvalCase) -> None:
    build_setup(case)  # raises if a task/habit/routine/reminder/preference is malformed


def test_confirmation_flows_are_covered() -> None:
    flows = [c for c in CASES if any(t.expect.confirmation for t in c.turns)]
    assert len(flows) >= 3
    assert any(len(c.turns) == 2 and not c.turns[1].expect.tools for c in flows)  # a "no"


def test_bad_cases_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        ToolExpect(name="teleport")
    with pytest.raises(ValueError, match="no argument"):
        ToolExpect(name="create_task", args={"tittle": "x"})
    with pytest.raises(ValueError, match="no matching setup item"):
        EvalCase.model_validate(
            {"id": "x", "now": "2026-11-23T10:00:00+05:30",
             "turns": [{"say": "done", "expect": {"tools": [
                 {"name": "complete_task", "args": {"task_id": {"ref": "tasks.0"}}}]}}]}
        )  # fmt: skip


# --- matchers -----------------------------------------------------------------------------


def test_values_compare_by_meaning() -> None:
    assert same("2026-11-27T17:00:00+05:30", "2026-11-27T11:30:00Z")
    assert not same("2026-11-27T17:00:00+05:30", "2026-11-27T17:00:00")  # naive never matches
    assert same("19:00", "19:00:00")
    assert same("Gym", " gym ")
    assert same({"start": "23:30", "end": "06:30"}, {"end": "06:30", "start": "23:30"})


def test_matcher_operators() -> None:
    refs = {"tasks.0": 7}
    assert arg_matches({"contains": "se"}, "Submit SE assignment", refs)
    assert arg_matches({"any_of": ["2026-11-23", None]}, None, refs)
    assert arg_matches({"present": True}, 5, refs) and not arg_matches(
        {"present": True}, None, refs
    )
    assert arg_matches({"present": False}, None, refs)
    assert arg_matches({"ref": "tasks.0"}, 7, refs) and not arg_matches({"ref": "tasks.0"}, 8, refs)
    assert arg_matches({"time": "07:00"}, {"time": "07:00"}, refs)  # not an operator: a value


# --- scoring ------------------------------------------------------------------------------


EXPECT = TurnExpect(
    intent="checkin",
    tools=[ToolExpect(name="complete_task", args={"task_id": {"ref": "tasks.0"}})],
)


def test_expected_tools_with_extra_reads_pass() -> None:
    seen = Observed(
        "checkin", [Call("find_task", {"query": "SE"}), Call("complete_task", {"task_id": 7})]
    )
    score = score_turn(EXPECT, seen, {"tasks.0": 7})
    assert score.intent_ok and score.tools_ok


def test_wrong_args_unexpected_writes_and_confirmation_are_problems() -> None:
    refs = {"tasks.0": 7}
    wrong = score_turn(EXPECT, Observed("capture", [Call("complete_task", {"task_id": 8})]), refs)
    assert not wrong.intent_ok and not wrong.tools_ok
    extra = score_turn(
        EXPECT,
        Observed("checkin", [Call("complete_task", {"task_id": 7}), Call("create_task", {})]),
        refs,
    )
    assert extra.problems == ["unexpected call to create_task"]
    asked = score_turn(
        TurnExpect(intent="manage"), Observed("manage", awaiting_confirmation=True), {}
    )
    assert asked.problems == ["asked to confirm"]


def test_alternative_argument_sets() -> None:
    expect = TurnExpect(
        intent="checkin",
        tools=[ToolExpect(name="log_habit", args=[
            {"habit_id": {"ref": "habits.0"}, "status": "done"},
            {"habit_name": {"contains": "workout"}, "status": "done"},
        ])],
    )  # fmt: skip
    refs = {"habits.0": 3}
    by_name = Observed("checkin", [Call("log_habit", {"habit_name": "Workout", "status": "done"})])
    by_id = Observed("checkin", [Call("log_habit", {"habit_id": 3, "status": "done"})])
    skipped = Observed("checkin", [Call("log_habit", {"habit_id": 3, "status": "skipped"})])
    assert score_turn(expect, by_name, refs).tools_ok and score_turn(expect, by_id, refs).tools_ok
    assert not score_turn(expect, skipped, refs).tools_ok


def test_a_rejected_call_is_not_an_unexpected_write() -> None:
    expect = TurnExpect(
        intent="preference",
        tools=[ToolExpect(name="set_preference", args={"key": "quiet_hours"})],
    )
    retried = Observed(
        "preference",
        [
            Call("set_preference", {"key": "quiet_hours", "value": "23:30-06:30"}, ok=False),
            Call("set_preference", {"key": "quiet_hours", "value": {"start": "23:30"}}),
        ],
    )
    assert score_turn(expect, retried, {}).tools_ok
    twice = Observed("preference", [Call("set_preference", {"key": "quiet_hours"})] * 2)
    assert score_turn(expect, twice, {}).problems == ["unexpected call to set_preference"]
