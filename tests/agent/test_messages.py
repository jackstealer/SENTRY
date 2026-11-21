from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from sentry_ai.agent.messages import HISTORY_MESSAGES, estimate_tokens, to_openai, trim

BIG = 10**9


def tool_turn(n: int, rounds: int = 1) -> list[AnyMessage]:
    turn: list[AnyMessage] = [HumanMessage(f"q{n}")]
    for r in range(rounds):
        turn.append(
            AIMessage("", tool_calls=[{"name": "list_tasks", "args": {}, "id": f"c{n}-{r}"}])
        )
        turn.append(ToolMessage("{}", tool_call_id=f"c{n}-{r}", name="list_tasks"))
    turn.append(AIMessage(f"a{n}"))
    return turn


def contents(messages: list[AnyMessage]) -> list[str]:
    return [str(m.content) for m in messages if isinstance(m, HumanMessage)]


def test_keeps_whole_recent_turns_up_to_twelve_messages() -> None:
    history = [m for n in range(5) for m in tool_turn(n)]  # 4 messages per turn
    kept = trim(history, budget_chars=BIG)
    assert contents(kept) == ["q2", "q3", "q4"]  # 12 messages
    assert len(kept) == HISTORY_MESSAGES
    assert isinstance(kept[0], HumanMessage)


def test_never_separates_a_tool_call_from_its_result() -> None:
    history = [m for n in range(6) for m in tool_turn(n, rounds=2)]
    kept = trim(history, budget_chars=BIG)
    payload = to_openai(kept)
    call_ids = [
        c["id"] for m in payload if m["role"] == "assistant" for c in m.get("tool_calls", [])
    ]
    result_ids = [m["tool_call_id"] for m in payload if m["role"] == "tool"]
    assert call_ids == result_ids


def test_the_current_turn_survives_even_when_it_is_long() -> None:
    history = tool_turn(0) + tool_turn(1, rounds=6)  # current turn: 14 messages
    kept = trim(history, budget_chars=10)
    assert contents(kept) == ["q1"] and len(kept) == 14


def test_the_size_budget_drops_the_oldest_turns_first() -> None:
    old = [HumanMessage("x" * 4000), AIMessage("ok")]
    recent = [HumanMessage("what's on today?"), AIMessage("Gym at 6.")]
    current = [HumanMessage("and tomorrow?")]
    kept = trim(old + recent + current, budget_chars=500)
    assert contents(kept) == ["what's on today?", "and tomorrow?"]
    assert trim([], budget_chars=BIG) == []
    assert estimate_tokens("x" * 400) == 100
