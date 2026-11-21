"""Runs one tool call: validate -> confirmation gate -> run in a savepoint -> ToolResult -> log.

Each call runs in its own savepoint, so a tool that fails or crashes undoes only its own
writes. The caller (one agent turn) owns the outer transaction and commits once at the end.
"""

import time
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ValidationError

from sentry_ai.observability.logging import get_logger
from sentry_ai.tools.base import Tool, ToolContext, ToolResult, ToolTier, fail
from sentry_ai.tools.registry import TOOLS

log = get_logger(__name__)

CONFIRMATION_TTL = timedelta(minutes=10)  # TOOLS_SPEC section 2


async def execute(
    name: str,
    raw_args: Mapping[str, Any],
    ctx: ToolContext,
    *,
    tools: Mapping[str, Tool] | None = None,
) -> ToolResult:
    """Never raises for tool problems: every outcome comes back as a ToolResult."""
    bound = log.bind(trace_id=ctx.trace_id, tool=name)
    found = (TOOLS if tools is None else tools).get(name)
    if found is None:
        bound.warning("tool_unknown")
        return fail("unknown_tool", f"There is no tool called {name!r}.")
    # keys only: argument values can be personal (CONVENTIONS section 4)
    bound.info("tool_called", tier=found.tier, arg_keys=sorted(raw_args))
    started = time.perf_counter()
    try:
        args = found.input_model.model_validate(dict(raw_args))
    except ValidationError as exc:
        result = fail("invalid_args", explain(exc))
    else:
        result = await _run(found, args, ctx)
    bound.info(
        "tool_completed",
        ok=result.ok,
        error=result.error,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return result


async def _run(tool: Tool, args: BaseModel, ctx: ToolContext) -> ToolResult:
    savepoint = await ctx.session.begin_nested()
    try:
        if tool.tier is ToolTier.DESTRUCTIVE and not ctx.confirmed:
            result = await _ask_first(tool, args, ctx)
        else:
            result = await tool.run(args, ctx)
    except ValidationError as exc:  # a DTO built inside the tool rejected the input
        result = fail("invalid_args", explain(exc))
    except Exception:
        log.exception("tool_failed", trace_id=ctx.trace_id, tool=tool.name)
        result = fail("internal_error", f"Something went wrong (trace {ctx.trace_id}).")
    if result.ok:
        await savepoint.commit()
    else:
        await savepoint.rollback()
    return result


async def _ask_first(tool: Tool, args: BaseModel, ctx: ToolContext) -> ToolResult:
    if tool.preview is None:  # tool() refuses to build such a tool; kept for type narrowing
        raise TypeError(f"{tool.name} is destructive but has no preview")
    summary = await tool.preview(args, ctx)
    return fail(
        "confirmation_required",
        f"{summary} Proceed?",
        pending={
            "tool": tool.name,
            "args": args.model_dump(mode="json", exclude_unset=True),
            "summary": summary,
            "expires_at": ctx.now + CONFIRMATION_TTL,
        },
    )


def explain(exc: ValidationError) -> str:
    """One line per problem, e.g. "due_at: Input should have timezone info"."""
    parts = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error["loc"])
        parts.append(f"{where}: {error['msg']}" if where else error["msg"])
    return "Invalid arguments: " + "; ".join(parts)
