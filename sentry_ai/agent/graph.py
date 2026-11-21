"""The agent graph (ARCHITECTURE section 3), its Postgres checkpointer, and one-turn runner.

START -> load_context -> route -+-(confirmation pending + "yes")-> run_pending -> respond
                                +-> act -+-(tool calls)-> tools -+-> act       (<= 6 rounds)
                                         |                       +-(confirmation needed)-> confirm
                                         +-(answer / guard)-> respond
confirm -> persist -> END;  respond -> persist -> END
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.engine import make_url

from sentry_ai.agent import nodes
from sentry_ai.agent.context import load_context
from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.state import AgentState, turn_input
from sentry_ai.config import get_settings
from sentry_ai.observability.logging import bind_trace, get_logger
from sentry_ai.services.time import local_date_of

log = get_logger(__name__)

Graph = CompiledStateGraph[AgentState, AgentDeps, AgentState, AgentState]


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> Graph:
    g = StateGraph(AgentState, context_schema=AgentDeps)
    g.add_node("load_context", load_context)
    g.add_node("route", nodes.route)
    g.add_node("act", nodes.act)
    g.add_node("tools", nodes.tools)
    g.add_node("confirm", nodes.confirm)
    g.add_node("run_pending", nodes.run_pending)
    g.add_node("respond", nodes.respond)
    g.add_node("persist", nodes.persist)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "route")
    g.add_conditional_edges("route", nodes.after_route, ["run_pending", "act"])
    g.add_conditional_edges("act", nodes.after_act, ["tools", "respond"])
    g.add_conditional_edges("tools", nodes.after_tools, ["confirm", "respond", "act"])
    g.add_edge("run_pending", "respond")
    g.add_edge("confirm", "persist")
    g.add_edge("respond", "persist")
    g.add_edge("persist", END)
    return g.compile(checkpointer=checkpointer)


def thread_id(user_id: str, day: date) -> str:
    """One conversation thread per user per IST day: context stays small and cheap."""
    return f"{user_id}:{day.isoformat()}"


@asynccontextmanager
async def postgres_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """LangGraph's saver on our database (psycopg). It creates its own checkpoint tables.

    On Windows, psycopg's async mode needs a selector event loop (see sentry_ai/cli.py).
    """
    url = make_url(get_settings().database_url).set(drivername="postgresql")
    async with AsyncPostgresSaver.from_conn_string(
        url.render_as_string(hide_password=False)
    ) as saver:
        await saver.setup()
        yield saver


@dataclass(frozen=True)
class TurnResult:
    trace_id: str
    reply: str
    intent: str | None
    awaiting_confirmation: bool


async def run_turn(
    graph: Graph,
    text: str,
    *,
    deps: AgentDeps,
    user_id: str | None = None,
    trace_id: str | None = None,
    mode: Literal["reactive", "proactive"] = "reactive",
    trigger: str | None = None,
) -> TurnResult:
    """One inbound message through the graph, with a fresh trace id bound for every log line."""
    user_id = user_id or get_settings().user_id
    trace_id = bind_trace(trace_id)
    log.info("turn_started", mode=mode, trigger=trigger)
    config: Any = {
        "configurable": {"thread_id": thread_id(user_id, local_date_of(deps.clock()))},
        "recursion_limit": 40,  # 6 tool rounds use ~16 steps
    }
    state = await graph.ainvoke(
        turn_input(text, trace_id=trace_id, user_id=user_id, mode=mode, trigger=trigger),
        config,
        context=deps,
    )
    return TurnResult(
        trace_id=trace_id,
        reply=state.get("final_response") or "",
        intent=state.get("intent"),
        awaiting_confirmation=state.get("pending_confirmation") is not None,
    )
