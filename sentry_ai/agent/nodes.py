"""Graph nodes (ARCHITECTURE section 3): route -> act <-> tools -> respond -> persist, plus the
confirm / run_pending pair that pauses a destructive tool for the user's yes."""

import json
import re
from datetime import datetime
from typing import Any, Literal

import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict

from sentry_ai.agent import llm
from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.messages import UNPARSED, from_openai, to_openai
from sentry_ai.agent.prompting import render
from sentry_ai.agent.state import AgentState, PendingConfirmation
from sentry_ai.db.repositories import MemoryRepo
from sentry_ai.observability.logging import get_logger
from sentry_ai.tools import registry
from sentry_ai.tools.base import ToolResult, fail
from sentry_ai.tools.executor import execute
from sentry_ai.tools.registry import Intent

log = get_logger(__name__)

MAX_TOOL_ROUNDS = 6  # loop guard (ARCHITECTURE section 3)
FAST_INTENTS: frozenset[Intent] = frozenset({"capture", "checkin", "smalltalk"})
TOOL_RESULT_CHARS = 2000  # what one tool result may add to the next prompt


class RouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    confirmation: Literal["yes", "no", "none"]


ROUTE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "route_decision",
        "strict": True,
        "schema": RouteDecision.model_json_schema(),
    },
}


def _this_turn(state: AgentState) -> list[AnyMessage]:
    """Messages since the latest user message, that message included."""
    messages = state.get("messages", [])
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return list(messages)


def _expired(pending: PendingConfirmation, now: datetime) -> bool:
    return datetime.fromisoformat(pending["expires_at"]) <= now


# --- route ---------------------------------------------------------------------------------


async def route(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Classify the turn (FAST model, structured output) and settle any pending confirmation."""
    pending = state.get("pending_confirmation")
    if pending is not None and _expired(pending, runtime.context.clock()):
        log.info("confirmation_expired", tool=pending["tool"])
        pending = None
    turn = _this_turn(state)
    earlier = state.get("messages", [])[: len(state.get("messages", [])) - len(turn)]
    previous = [m for m in earlier if isinstance(m, AIMessage) and m.content][-1:]
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": render("route", pending=pending["summary"] if pending else None),
        },
        *to_openai([*previous, turn[0]] if turn else previous),
    ]
    try:
        response = await llm.chat(
            messages, purpose="route", tier="fast", temperature=0, response_format=ROUTE_FORMAT
        )
        decision = RouteDecision.model_validate_json(response.choices[0].message.content or "")
    except (openai.OpenAIError, llm.LLMNotConfiguredError, ValueError) as exc:
        log.warning("route_failed", error_type=type(exc).__name__)
        decision = RouteDecision(intent="other", confirmation="none")
    log.info("route_decided", intent=decision.intent, confirmation=decision.confirmation)
    keep = pending if pending is not None and decision.confirmation == "yes" else None
    return {"intent": decision.intent, "pending_confirmation": keep}


def after_route(state: AgentState) -> Literal["run_pending", "act"]:
    return "run_pending" if state.get("pending_confirmation") else "act"


# --- act <-> tools -------------------------------------------------------------------------


async def act(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """One tool-calling LLM step with only the routed intent's tools."""
    intent: Intent = state.get("intent") or "other"
    system = render("system", **state["context"])
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system},
        *to_openai(state.get("messages", [])),
    ]
    try:
        response = await llm.chat(
            messages,
            purpose="act",
            tier="fast" if intent in FAST_INTENTS else "reasoning",
            tools=registry.openai_schemas(intent),
            temperature=0.3,
        )
    except llm.LLMNotConfiguredError as exc:
        return _reply(f"I'm not set up to think yet: {exc}. Add it to .env.")
    except openai.OpenAIError as exc:
        log.error("act_failed", error_type=type(exc).__name__)
        return _reply(f"I can't reach my language model right now (trace {state['trace_id']}).")
    return {"messages": [from_openai(response.choices[0].message)]}


def _reply(text: str) -> dict[str, Any]:
    return {"final_response": text, "messages": [AIMessage(text)]}


def after_act(state: AgentState) -> Literal["tools", "respond"]:
    last = state.get("messages", [])[-1:]
    wants_tools = bool(last) and isinstance(last[0], AIMessage) and bool(last[0].tool_calls)
    return "tools" if wants_tools and not state.get("final_response") else "respond"


def _tool_content(result: ToolResult) -> str:
    full = json.dumps(result.model_dump(), ensure_ascii=False)
    if len(full) <= TOOL_RESULT_CHARS:
        return full
    short = {"ok": result.ok, "message": result.message, "error": result.error}
    room = TOOL_RESULT_CHARS - len(json.dumps(short)) - 40
    data = json.dumps(result.data, ensure_ascii=False)[: max(room, 0)]
    return json.dumps(short | {"data_truncated": data})


async def tools(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Run the requested tool calls; the DB commits once this step is done (ADR-002)."""
    deps = runtime.context
    call_message = state["messages"][-1]
    assert isinstance(call_message, AIMessage)
    allowed = {t.name for t in registry.tools_for(state.get("intent") or "other")}
    results: list[ToolMessage] = []
    pending: dict[str, Any] | None = None
    async with deps.sessions() as session:
        ctx = deps.tool_context(session, user_id=state["user_id"], trace_id=state["trace_id"])
        for call in call_message.tool_calls:
            if call["name"] not in allowed:
                result = fail("not_allowed", f"{call['name']} is not available for this request.")
            elif UNPARSED in call["args"]:
                result = fail("invalid_args", "Arguments were not valid JSON; send a JSON object.")
            elif pending is not None:
                result = fail(
                    "not_run", "Skipped: waiting for the user to confirm the last action."
                )
            else:
                result = await execute(call["name"], call["args"], ctx)
                if result.error == "confirmation_required":
                    pending = result.data["pending"]
            results.append(
                ToolMessage(_tool_content(result), tool_call_id=call["id"] or "", name=call["name"])
            )
    update: dict[str, Any] = {
        "messages": results,
        "tool_calls_count": state.get("tool_calls_count", 0) + 1,
    }
    if pending is not None:
        update["pending_confirmation"] = pending
    return update


def after_tools(state: AgentState) -> Literal["confirm", "respond", "act"]:
    if state.get("pending_confirmation"):
        return "confirm"
    if state.get("tool_calls_count", 0) >= MAX_TOOL_ROUNDS:
        log.warning("tool_loop_guard", rounds=state.get("tool_calls_count"))
        return "respond"
    return "act"


# --- confirmation --------------------------------------------------------------------------


def confirm(state: AgentState) -> dict[str, Any]:
    """Ask, then end the turn; the pending call waits in the checkpoint (TOOLS_SPEC section 2)."""
    pending = state["pending_confirmation"]
    assert pending is not None
    log.info("confirmation_requested", tool=pending["tool"])
    return _reply(f"{pending['summary']} Proceed? (yes/no)")


async def run_pending(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """The user said yes: run the paused call with confirmed=True."""
    deps, pending = runtime.context, state["pending_confirmation"]
    assert pending is not None
    async with deps.sessions() as session:
        ctx = deps.tool_context(
            session, user_id=state["user_id"], trace_id=state["trace_id"], confirmed=True
        )
        result = await execute(pending["tool"], pending["args"], ctx)
    log.info("confirmation_executed", tool=pending["tool"], ok=result.ok)
    text = result.message if result.ok else f"That didn't go through: {result.message}"
    return {"pending_confirmation": None, "tool_calls_count": 1} | _reply(text)


# --- respond / persist ---------------------------------------------------------------------

_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _tool_summaries(turn: list[AnyMessage]) -> list[str]:
    done = []
    for message in turn:
        if isinstance(message, ToolMessage):
            try:
                body = json.loads(str(message.content))
            except json.JSONDecodeError:
                continue
            if body.get("ok") and body.get("message"):
                done.append(str(body["message"]))
    return done


def respond(state: AgentState) -> dict[str, Any]:
    """Final text for the user: the model's answer, or a summary if the loop guard stopped it."""
    if state.get("final_response"):
        return {"final_response": _HEADER.sub("", str(state["final_response"])).strip()}
    turn = _this_turn(state)
    last = turn[-1] if turn else None
    if isinstance(last, AIMessage) and not last.tool_calls and last.content:
        return {"final_response": _HEADER.sub("", str(last.content)).strip()}
    done = _tool_summaries(turn)
    text = "I stopped there to avoid going in circles." + (
        " Done so far: " + " ".join(done) if done else " Nothing was changed."
    )
    return _reply(text)


async def persist(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    """Episodic log of the turn (ARCHITECTURE section 8); memory extraction comes later."""
    turn = _this_turn(state)
    used = [
        {"tool": m.name, "ok": json.loads(str(m.content)).get("ok")}
        for m in turn
        if isinstance(m, ToolMessage)
    ]
    meta = {
        "intent": state.get("intent"),
        "mode": state.get("mode"),
        "trigger": state.get("trigger"),
    }
    async with runtime.context.sessions() as session:
        repo = MemoryRepo(session, state["user_id"])
        if turn and isinstance(turn[0], HumanMessage):
            await repo.log_episode(state["trace_id"], "user", str(turn[0].content), meta)
        if used:
            names = ", ".join(f"{u['tool']}({'ok' if u['ok'] else 'failed'})" for u in used)
            await repo.log_episode(state["trace_id"], "tool", names, meta | {"tools": used})
        reply = state.get("final_response") or ""
        await repo.log_episode(state["trace_id"], "assistant", reply, meta)
    log.info(
        "turn_completed",
        intent=state.get("intent"),
        tool_rounds=state.get("tool_calls_count", 0),
        tools=[u["tool"] for u in used],
    )
    return {}
