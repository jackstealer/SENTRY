from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.services.reminders import NoopReminderScheduler, ReminderScheduler
from sentry_ai.tools.base import (
    ToolArgs,
    ToolContext,
    ToolResult,
    ToolTier,
    fail,
    ok,
    to_data,
    tool,
)


class NoArgs(ToolArgs):
    pass


async def _preview(args: NoArgs, ctx: ToolContext) -> str:
    return "This will do it."


def test_tool_decorator_uses_name_docstring_and_module() -> None:
    @tool(ToolTier.READ, NoArgs)
    async def look_around(args: NoArgs, ctx: ToolContext) -> ToolResult:
        """Use when asked to look around.

        Not for anything else.
        """
        return ok("looked")

    assert look_around.name == "look_around"
    assert look_around.description == "Use when asked to look around.\n\nNot for anything else."
    assert look_around.tier is ToolTier.READ
    assert look_around.category == "test_base"
    assert look_around.preview is None


def test_destructive_tools_and_only_they_need_a_preview() -> None:
    async def run(args: NoArgs, ctx: ToolContext) -> ToolResult:
        """Doc."""
        return ok("")

    with pytest.raises(TypeError, match="need a preview"):
        tool(ToolTier.DESTRUCTIVE, NoArgs)(run)
    with pytest.raises(TypeError, match="need a preview"):
        tool(ToolTier.WRITE, NoArgs, preview=_preview)(run)
    assert tool(ToolTier.DESTRUCTIVE, NoArgs, preview=_preview)(run).preview is _preview


def test_a_tool_without_a_docstring_is_rejected() -> None:
    async def silent(args: NoArgs, ctx: ToolContext) -> ToolResult:
        return ok("")

    with pytest.raises(TypeError, match="docstring"):
        tool(ToolTier.READ, NoArgs)(silent)


def test_tool_args_forbid_unknown_keys_and_strip_strings() -> None:
    class Args(ToolArgs):
        title: str

    assert Args.model_validate({"title": "  read  "}).title == "read"
    with pytest.raises(ValidationError):
        Args.model_validate({"title": "x", "titel": "y"})


async def test_context_converts_now_to_user_tz_and_rejects_naive(session: AsyncSession) -> None:
    ctx = ToolContext(
        user_id="u", trace_id="t", now=datetime(2026, 11, 22, 18, 20, tzinfo=UTC), session=session
    )
    assert ctx.now.isoformat() == "2026-11-22T23:50:00+05:30"
    assert ctx.today == date(2026, 11, 22)  # 23:50 IST is still the 22nd
    assert isinstance(ctx.reminders, NoopReminderScheduler)
    assert isinstance(ctx.reminders, ReminderScheduler)
    with pytest.raises(ValidationError):
        ToolContext(user_id="u", trace_id="t", now=datetime(2026, 11, 22, 18, 20), session=session)


def test_to_data_is_json_safe_and_local() -> None:
    out = to_data(
        {
            "at": datetime(2026, 11, 22, 19, 0, tzinfo=UTC),  # 00:30 IST next day
            "day": date(2026, 11, 23),
            "time": time(9, 5),
            "cost": Decimal("0.25"),
            "items": ({"n": 1},),
        }
    )
    assert out == {
        "at": "2026-11-23T00:30+05:30",
        "day": "2026-11-23",
        "time": "09:05",
        "cost": 0.25,
        "items": [{"n": 1}],
    }


def test_ok_and_fail_helpers() -> None:
    assert ok("done", n=1) == ToolResult(ok=True, message="done", data={"n": 1})
    result = fail("not_found", "No task 9.")
    assert (result.ok, result.error, result.message, result.data) == (
        False,
        "not_found",
        "No task 9.",
        {},
    )
