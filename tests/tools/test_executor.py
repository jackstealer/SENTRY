from datetime import UTC, datetime

import pytest
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from sentry_ai.db.repositories import PreferenceRepo
from sentry_ai.db.schemas import TaskCreate
from sentry_ai.tools.base import Tool, ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool
from sentry_ai.tools.executor import execute

NOW = datetime(2026, 11, 22, 18, 20, tzinfo=UTC)  # 23:50 IST


class KeyArgs(ToolArgs):
    key: str = Field(min_length=1)


async def _save(args: KeyArgs, ctx: ToolContext) -> None:
    await PreferenceRepo(ctx.session, ctx.user_id).set(args.key, {"v": 1})


@tool(ToolTier.WRITE, KeyArgs)
async def save_ok(args: KeyArgs, ctx: ToolContext) -> ToolResult:
    """Saves and succeeds."""
    await _save(args, ctx)
    return ok("saved", key=args.key)


@tool(ToolTier.WRITE, KeyArgs)
async def save_then_fail(args: KeyArgs, ctx: ToolContext) -> ToolResult:
    """Saves, then reports an expected failure."""
    await _save(args, ctx)
    return fail("not_found", "nothing there")


@tool(ToolTier.WRITE, KeyArgs)
async def save_then_crash(args: KeyArgs, ctx: ToolContext) -> ToolResult:
    """Saves, then hits a bug."""
    await _save(args, ctx)
    raise RuntimeError("boom with a secret value")


@tool(ToolTier.WRITE, KeyArgs)
async def builds_bad_dto(args: KeyArgs, ctx: ToolContext) -> ToolResult:
    """Builds a DTO that rejects its input."""
    TaskCreate(title="")
    return ok("unreachable")


async def _preview(args: KeyArgs, ctx: ToolContext) -> str:
    return f"This will wipe {args.key}."


@tool(ToolTier.DESTRUCTIVE, KeyArgs, preview=_preview)
async def wipe(args: KeyArgs, ctx: ToolContext) -> ToolResult:
    """Destructive."""
    await _save(args, ctx)
    return ok("wiped")


TOOLS: dict[str, Tool] = {
    t.name: t for t in (save_ok, save_then_fail, save_then_crash, builds_bad_dto, wipe)
}


@pytest.fixture
def ctx(session: AsyncSession, user_id: str) -> ToolContext:
    return ToolContext(user_id=user_id, trace_id="trace-1", now=NOW, session=session)


async def stored(ctx: ToolContext, key: str) -> bool:
    return await PreferenceRepo(ctx.session, ctx.user_id).get(key) is not None


async def test_happy_path_keeps_the_write(ctx: ToolContext) -> None:
    result = await execute("save_ok", {"key": "k1"}, ctx, tools=TOOLS)
    assert result == ToolResult(ok=True, message="saved", data={"key": "k1"})
    assert await stored(ctx, "k1")


async def test_unknown_tool(ctx: ToolContext) -> None:
    result = await execute("teleport", {}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "unknown_tool")


async def test_invalid_args_never_reach_the_tool(ctx: ToolContext) -> None:
    result = await execute("save_ok", {"key": "", "extra": 1}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "invalid_args")
    assert "key: String should have at least 1 character" in result.message
    assert "extra: Extra inputs are not permitted" in result.message
    assert not await stored(ctx, "")


async def test_expected_failure_rolls_back_only_that_call(ctx: ToolContext) -> None:
    await execute("save_ok", {"key": "kept"}, ctx, tools=TOOLS)
    result = await execute("save_then_fail", {"key": "undone"}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "not_found")
    assert await stored(ctx, "kept")
    assert not await stored(ctx, "undone")


async def test_crash_becomes_internal_error_with_trace_id(ctx: ToolContext) -> None:
    with capture_logs() as logs:
        result = await execute("save_then_crash", {"key": "k"}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "internal_error")
    assert result.message == "Something went wrong (trace trace-1)."
    assert "secret" not in result.message
    assert not await stored(ctx, "k")
    failed = [e for e in logs if e["event"] == "tool_failed"]
    assert failed and failed[0]["exc_info"] is True


async def test_validation_error_inside_a_tool_is_invalid_args(ctx: ToolContext) -> None:
    result = await execute("builds_bad_dto", {"key": "k"}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "invalid_args")
    assert "title" in result.message


async def test_destructive_tool_refuses_without_confirmation(ctx: ToolContext) -> None:
    result = await execute("wipe", {"key": "k"}, ctx, tools=TOOLS)
    assert (result.ok, result.error) == (False, "confirmation_required")
    assert result.message == "This will wipe k. Proceed?"
    assert result.data["pending"] == {
        "tool": "wipe",
        "args": {"key": "k"},
        "summary": "This will wipe k.",
        "expires_at": "2026-11-23T00:00+05:30",  # now (23:50 IST) + 10 minutes
    }
    assert not await stored(ctx, "k")


async def test_destructive_tool_runs_once_confirmed(ctx: ToolContext) -> None:
    confirmed = ctx.model_copy(update={"confirmed": True})
    result = await execute("wipe", {"key": "k"}, confirmed, tools=TOOLS)
    assert result.ok
    assert await stored(ctx, "k")


async def test_every_call_is_logged_with_trace_and_without_values(ctx: ToolContext) -> None:
    with capture_logs() as logs:
        await execute("save_ok", {"key": "private-value"}, ctx, tools=TOOLS)
    events = {e["event"]: e for e in logs}
    assert events["tool_called"]["trace_id"] == "trace-1"
    assert events["tool_called"]["arg_keys"] == ["key"]
    assert events["tool_completed"]["ok"] is True
    assert isinstance(events["tool_completed"]["latency_ms"], int)
    assert "private-value" not in str(logs)
