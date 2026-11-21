"""Every tool, found by name, grouped by intent, exported as OpenAI function definitions."""

import types
from collections.abc import Iterable
from typing import Any, Literal

from sentry_ai.tools import habits, memory, routines, tasks
from sentry_ai.tools.base import Tool, ToolTier

Intent = Literal[
    "capture", "checkin", "query", "plan", "review", "preference", "manage", "smalltalk", "other"
]

# TOOLS_SPEC section 5. `act` only sees its intent's subset; "other" gets every read tool.
INTENTS: dict[Intent, tuple[str, ...]] = {
    "capture": ("create_task", "create_habit", "create_routine", "create_reminder", "find_task"),
    "checkin": (
        "find_task",
        "complete_task",
        "skip_task",
        "log_habit",
        "ack_reminder",
        "snooze_reminder",
    ),
    "query": (
        "get_today_overview",
        "list_tasks",
        "get_habits_status",
        "get_calendar_events",
        "get_free_slots",
        "list_routines",
    ),
    "plan": (
        "get_today_overview",
        "get_free_slots",
        "list_tasks",
        "propose_day_plan",
        "confirm_day_plan",
        "move_block",
    ),
    "review": ("get_stats", "get_habits_status", "list_tasks", "update_task", "recall"),
    "preference": ("set_preference", "get_preferences", "remember", "recall", "forget"),
    "manage": (
        "find_task",
        "list_tasks",
        "update_task",
        "get_habits_status",
        "update_habit",
        "list_routines",
        "update_routine",
        "cancel_reminder",
        "archive_tasks",
        "delete_habit",
        "delete_routine",
    ),
    "smalltalk": ("recall",),
    "other": (),
}


def collect(modules: Iterable[types.ModuleType]) -> dict[str, Tool]:
    """Every Tool defined in the given modules, by name; two different tools may not share one."""
    found: dict[str, Tool] = {}
    for module in modules:
        for obj in vars(module).values():
            if isinstance(obj, Tool) and found.setdefault(obj.name, obj) is not obj:
                raise ValueError(f"two tools are named {obj.name!r}")
    return found


_MODULES: tuple[types.ModuleType, ...] = (tasks, habits, routines, memory)
TOOLS: dict[str, Tool] = collect(_MODULES)


def get(name: str) -> Tool | None:
    return TOOLS.get(name)


def tools_for(intent: Intent) -> list[Tool]:
    if intent == "other":
        return [t for t in TOOLS.values() if t.tier is ToolTier.READ]
    return [TOOLS[name] for name in INTENTS[intent]]


def openai_schema(tool: Tool) -> dict[str, Any]:
    """Chat Completions `tools` entry; the input model's JSON schema is the parameter spec."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_model.model_json_schema(),
        },
    }


def openai_schemas(intent: Intent) -> list[dict[str, Any]]:
    return [openai_schema(t) for t in tools_for(intent)]
