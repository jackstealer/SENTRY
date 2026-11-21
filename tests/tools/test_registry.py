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
