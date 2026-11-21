from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import add_messages

from sentry_ai.agent.state import AgentState, turn_input


def test_turn_input_resets_per_turn_fields_only() -> None:
    state = turn_input("plan my day", trace_id="t1", user_id="me")
    assert state["messages"][0].content == "plan my day"
    assert isinstance(state["messages"][0], HumanMessage)
    assert (state["mode"], state["trigger"], state["tool_calls_count"]) == ("reactive", None, 0)
    assert (state["intent"], state["final_response"]) == (None, None)
    # the day's history and a pending confirmation must survive a new turn
    assert "pending_confirmation" not in state
    assert "context" not in state


def test_proactive_turns_carry_their_trigger() -> None:
    state = turn_input("", trace_id="t2", user_id="me", mode="proactive", trigger="morning_brief")
    assert (state["mode"], state["trigger"]) == ("proactive", "morning_brief")


def test_messages_accumulate_through_the_reducer() -> None:
    first: AgentState = {"messages": [HumanMessage("hi", id="1"), AIMessage("Evening.", id="2")]}
    merged = add_messages(
        first["messages"], turn_input("done gym", trace_id="t", user_id="me")["messages"]
    )
    assert [m.content for m in merged] == ["hi", "Evening.", "done gym"]
