"""What flows through the agent graph (ARCHITECTURE section 3).

The state is checkpointed per user per IST day, so it must stay JSON-serialisable. Live
objects (DB sessions, the scheduler) travel in the runtime context instead (agent/deps.py).
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages

from sentry_ai.tools.registry import Intent


class PendingConfirmation(TypedDict):
    tool: str
    args: dict[str, Any]
    summary: str
    expires_at: str  # ISO 8601 with offset


class AgentState(TypedDict, total=False):
    trace_id: str
    user_id: str
    mode: Literal["reactive", "proactive"]
    trigger: str | None  # e.g. "morning_brief", "reminder:123"
    messages: Annotated[list[AnyMessage], add_messages]
    context: dict[str, Any]  # today's snapshot, preferences, now (IST)
    memories: list[dict[str, Any]]  # retrieved semantic memories
    intent: Intent | None  # set by `route` for this turn
    pending_confirmation: PendingConfirmation | None
    tool_calls_count: int  # loop guard: tool rounds this turn
    final_response: str | None
    degraded: bool  # the LLM failed this turn: proactive jobs then send a plain template


def turn_input(
    text: str,
    *,
    trace_id: str,
    user_id: str,
    mode: Literal["reactive", "proactive"] = "reactive",
    trigger: str | None = None,
) -> AgentState:
    """Input for one turn; per-turn fields reset, the day's messages and pending stay."""
    return AgentState(
        trace_id=trace_id,
        user_id=user_id,
        mode=mode,
        trigger=trigger,
        messages=[HumanMessage(text)],
        intent=None,
        tool_calls_count=0,
        final_response=None,
        degraded=False,
    )
