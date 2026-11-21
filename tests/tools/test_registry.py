import json
import re
import types
from datetime import datetime
from typing import Any, Literal

import pytest
from pydantic import AwareDatetime, Field

from sentry_ai.tools import registry
from sentry_ai.tools.base import Tool, ToolArgs, ToolContext, ToolResult, ToolTier, ok, tool

NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def assert_valid_openai_tool(entry: dict[str, Any]) -> None:
    """Structure of a Chat Completions `tools` entry, checked offline."""
    assert set(entry) == {"type", "function"}
    assert entry["type"] == "function"
    fn = entry["function"]
    assert set(fn) == {"name", "description", "parameters"}
    assert NAME.match(fn["name"]), fn["name"]
    assert isinstance(fn["description"], str) and fn["description"].strip()
    params = fn["parameters"]
    assert params["type"] == "object"
    properties = params.get("properties", {})
    assert isinstance(properties, dict)
    assert set(params.get("required", [])) <= set(properties)
    defs = params.get("$defs", {})
    for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(params)):
        assert ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in defs, ref
    for schema in [*properties.values(), *defs.values()]:
        assert set(schema) & {"type", "$ref", "anyOf", "oneOf", "allOf", "enum", "const"}, schema
    json.dumps(entry)


class WhenArgs(ToolArgs):
    at: AwareDatetime
    mode: Literal["a", "b"] = "a"
    tags: list[str] = Field(default_factory=list, description="labels")


@tool(ToolTier.READ, WhenArgs)
async def fake_read(args: WhenArgs, ctx: ToolContext) -> ToolResult:
    """Read something."""
    return ok("read")


@tool(ToolTier.WRITE, WhenArgs)
async def fake_write(args: WhenArgs, ctx: ToolContext) -> ToolResult:
    """Write something."""
    return ok("wrote")


def module(**tools: Tool) -> types.ModuleType:
    mod = types.ModuleType("fake")
    vars(mod).update(tools, not_a_tool=42)
    return mod


def test_collect_finds_tools_by_name_and_ignores_other_objects() -> None:
    found = registry.collect([module(a=fake_read), module(b=fake_write, again=fake_read)])
    assert found == {"fake_read": fake_read, "fake_write": fake_write}


def test_collect_rejects_two_tools_with_one_name() -> None:
    clash = Tool("fake_read", "other", ToolTier.READ, WhenArgs, fake_read.run)
    with pytest.raises(ValueError, match="two tools are named 'fake_read'"):
        registry.collect([module(a=fake_read), module(b=clash)])


def test_openai_schema_is_a_valid_function_definition() -> None:
    entry = registry.openai_schema(fake_read)
    assert_valid_openai_tool(entry)
    params = entry["function"]["parameters"]
    assert params["required"] == ["at"]
    assert params["properties"]["at"]["format"] == "date-time"
    assert params["properties"]["mode"]["enum"] == ["a", "b"]
    assert params["additionalProperties"] is False


def test_tools_for_other_is_every_read_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "TOOLS", {"fake_read": fake_read, "fake_write": fake_write})
    assert registry.tools_for("other") == [fake_read]
    assert registry.get("fake_write") is fake_write
    assert registry.get("nope") is None


def test_intent_subsets_follow_tools_spec() -> None:
    assert set(registry.INTENTS) == set(registry.Intent.__args__)  # type: ignore[attr-defined]
    assert registry.INTENTS["checkin"][:3] == ("find_task", "complete_task", "skip_task")
    assert "forget" in registry.INTENTS["preference"]
    assert {"archive_tasks", "delete_habit", "delete_routine"} <= set(registry.INTENTS["manage"])


def test_datetime_args_parse_iso_with_offset() -> None:
    args = WhenArgs.model_validate({"at": "2026-11-22T23:50:00+05:30"})
    assert args.at == datetime.fromisoformat("2026-11-22T18:20:00+00:00")


# --- the real registry (TOOLS_SPEC sections 4-6) -------------------------------------------

PHASE_2_TOOLS = {
    # query
    "get_today_overview", "list_tasks", "get_habits_status", "get_free_slots", "get_stats",
    # tasks, habits, routines
    "create_task", "update_task", "complete_task", "skip_task", "find_task",
    "create_habit", "log_habit", "update_habit",
    "create_routine", "update_routine", "list_routines",
    # planning, reminders, calendar (cache only until Phase 6)
    "propose_day_plan", "confirm_day_plan", "move_block",
    "create_reminder", "snooze_reminder", "ack_reminder", "cancel_reminder",
    "get_calendar_events",
    # memory, preferences, housekeeping
    "remember", "recall", "set_preference", "get_preferences", "forget",
    "archive_tasks", "delete_habit", "delete_routine",
}  # fmt: skip
DESTRUCTIVE = {"forget", "archive_tasks", "delete_habit", "delete_routine"}


def test_every_phase_2_tool_is_registered() -> None:
    assert set(registry.TOOLS) == PHASE_2_TOOLS


def test_tiers_match_the_spec() -> None:
    tiers = {name: t.tier for name, t in registry.TOOLS.items()}
    assert {n for n, tier in tiers.items() if tier is ToolTier.DESTRUCTIVE} == DESTRUCTIVE
    reads = {n for n, tier in tiers.items() if tier is ToolTier.READ}
    assert reads == {
        "get_today_overview", "list_tasks", "get_habits_status", "get_free_slots", "get_stats",
        "find_task", "list_routines", "get_calendar_events", "recall", "get_preferences",
    }  # fmt: skip
    assert all(t.preview is not None for n, t in registry.TOOLS.items() if n in DESTRUCTIVE)


def test_every_intent_names_real_tools_and_every_change_is_reachable() -> None:
    reachable: set[str] = set()
    for intent in registry.INTENTS:
        names = [t.name for t in registry.tools_for(intent)]
        assert len(names) == len(set(names)), intent
        reachable |= set(names)
    assert reachable == PHASE_2_TOOLS  # nothing the LLM could never call


@pytest.mark.parametrize("intent", list(registry.INTENTS))
def test_openai_schemas_are_valid_for_every_intent(intent: registry.Intent) -> None:
    schemas = registry.openai_schemas(intent)
    assert schemas
    for entry in schemas:
        assert_valid_openai_tool(entry)
        assert entry["function"]["parameters"].get("additionalProperties") is False


@pytest.mark.parametrize("name", sorted(PHASE_2_TOOLS))
async def test_every_tool_rejects_unknown_arguments(call: Any, name: str) -> None:
    result = await call(name, confirmed=True, unexpected_argument=1)
    assert (result.ok, result.error) == (False, "invalid_args")
    assert "unexpected_argument" in result.message


def test_no_tool_advertises_a_strict_time_format() -> None:
    """`format: time` means "HH:MM:SS+offset"; providers that validate tool calls (Groq)
    then reject the "21:00" every model sends. Times are advertised as HH:MM strings."""
    for name, t in registry.TOOLS.items():
        assert '"format": "time"' not in json.dumps(t.input_model.model_json_schema()), name
    window = registry.TOOLS["propose_day_plan"].input_model.model_json_schema()["$defs"]["Window"]
    assert window["properties"]["start"]["pattern"] == r"^([01]\d|2[0-3]):[0-5]\d$"
