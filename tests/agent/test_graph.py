import uuid
from datetime import UTC, date, datetime

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from structlog.testing import capture_logs

from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.graph import build_graph, postgres_checkpointer, run_turn, thread_id
from sentry_ai.agent.nodes import MAX_TOOL_ROUNDS
from sentry_ai.db.repositories import TaskRepo
from sentry_ai.db.schemas import TaskCreate
from sentry_ai.services.time import local_date_of

from .conftest import NOW, FakeLLM

USER = "test-user"


async def turn(graph, deps, text):  # type: ignore[no-untyped-def]
    return await run_turn(graph, text, deps=deps, user_id=USER)


async def test_a_capture_turn_runs_end_to_end(fake_llm: FakeLLM, deps: AgentDeps, session) -> None:
    fake_llm.route("capture").call(
        ("create_task", {"title": "Submit SE assignment", "due_at": "2026-11-27T17:00:00+05:30"})
    ).say("Noted. 'Submit SE assignment' due Fri 5:00 PM.")
    graph = build_graph(InMemorySaver())
    with capture_logs() as logs:
        result = await turn(graph, deps, "remind me to submit SE assignment friday 5pm")
    assert result.reply == "Noted. 'Submit SE assignment' due Fri 5:00 PM."
    assert (result.intent, result.awaiting_confirmation) == ("capture", False)
    assert fake_llm.purposes() == ["route", "act", "act"]
    [task] = await TaskRepo(session, USER).query()
    assert task.due_at == datetime(2026, 11, 27, 11, 30, tzinfo=UTC)
    # one trace from start to finish: route -> tool calls -> response
    events = [e["event"] for e in logs]
    for name in (
        "turn_started",
        "route_decided",
        "tool_called",
        "tool_completed",
        "turn_completed",
    ):
        assert name in events
    assert {e.get("trace_id") for e in logs if e["event"] in ("tool_called", "tool_completed")} == {
        result.trace_id
    }


async def test_destructive_action_waits_for_yes_across_turns(
    fake_llm: FakeLLM, deps: AgentDeps, session
) -> None:
    tasks = TaskRepo(session, USER)
    done = await tasks.create(TaskCreate(title="old"))
    await tasks.set_status(done.id, "done", datetime(2026, 10, 1, tzinfo=UTC))
    fake_llm.route("manage").call(("archive_tasks", {"completed_before": "2026-11-23"}))
    graph = build_graph(InMemorySaver())

    asked = await turn(graph, deps, "delete all done tasks")
    assert asked.awaiting_confirmation
    assert (
        asked.reply
        == "This will archive 1 completed task(s) finished before 2026-11-23. Proceed? (yes/no)"
    )
    assert len(await tasks.query(status=["done"])) == 1  # nothing happened yet

    fake_llm.route("manage", "yes")
    confirmed = await turn(graph, deps, "yes")
    assert confirmed.reply == "Archived 1 completed task(s)."
    assert not confirmed.awaiting_confirmation
    assert await tasks.query(status=["done"]) == []
    assert fake_llm.purposes() == ["route", "act", "route"]  # no extra LLM call to execute


async def test_any_other_reply_drops_the_pending_action(
    fake_llm: FakeLLM, deps: AgentDeps, session
) -> None:
    tasks = TaskRepo(session, USER)
    done = await tasks.create(TaskCreate(title="old"))
    await tasks.set_status(done.id, "done", datetime(2026, 10, 1, tzinfo=UTC))
    fake_llm.route("manage").call(("archive_tasks", {"completed_before": "2026-11-23"}))
    graph = build_graph(InMemorySaver())
    await turn(graph, deps, "delete all done tasks")

    fake_llm.route("query", "no").say("Leaving them. You have nothing due today.")
    reply = await turn(graph, deps, "actually, what's on today?")
    assert reply.reply == "Leaving them. You have nothing due today."
    assert not reply.awaiting_confirmation
    assert len(await tasks.query(status=["done"])) == 1


async def test_the_loop_guard_stops_after_six_tool_rounds(
    fake_llm: FakeLLM, deps: AgentDeps
) -> None:
    fake_llm.route("query")
    for _ in range(MAX_TOOL_ROUNDS):
        fake_llm.call(("list_tasks", {}))
    result = await turn(build_graph(InMemorySaver()), deps, "what's pending?")
    assert fake_llm.purposes() == ["route"] + ["act"] * MAX_TOOL_ROUNDS
    assert result.reply.startswith(
        "I stopped there to avoid going in circles. Done so far: 0 task(s)."
    )


async def test_each_ist_day_gets_its_own_thread(fake_llm: FakeLLM, deps: AgentDeps) -> None:
    graph = build_graph(InMemorySaver())
    late = AgentDeps(
        sessions=deps.sessions,
        embed=deps.embed,
        clock=lambda: datetime(2026, 11, 22, 18, 20, tzinfo=UTC),
    )  # 23:50 IST Sun
    after = AgentDeps(
        sessions=deps.sessions,
        embed=deps.embed,
        clock=lambda: datetime(2026, 11, 22, 18, 40, tzinfo=UTC),
    )  # 00:10 IST Mon
    fake_llm.route("smalltalk").say("Evening.").route("smalltalk").say("Morning, technically.")
    await turn(graph, late, "hi")
    await turn(graph, after, "still up")
    assert thread_id(USER, date(2026, 11, 23)) == "test-user:2026-11-23"
    sunday = await graph.aget_state({"configurable": {"thread_id": "test-user:2026-11-22"}})
    monday = await graph.aget_state({"configurable": {"thread_id": "test-user:2026-11-23"}})
    assert [m.content for m in sunday.values["messages"]] == ["hi", "Evening."]
    assert [m.content for m in monday.values["messages"]] == ["still up", "Morning, technically."]


async def test_postgres_checkpointer_keeps_the_day_between_runs(
    fake_llm: FakeLLM, deps: AgentDeps
) -> None:
    user = f"pg-{uuid.uuid4().hex[:8]}"  # the saver commits for real: use a throwaway thread
    fake_llm.route("smalltalk").say("Evening.").route("smalltalk").say("Still here.")
    thread = thread_id(user, local_date_of(NOW))
    async with postgres_checkpointer() as saver:
        await run_turn(build_graph(saver), "hi", deps=deps, user_id=user)
    async with postgres_checkpointer() as saver:  # a restarted process would do exactly this
        graph = build_graph(saver)
        try:
            await run_turn(graph, "you there?", deps=deps, user_id=user)
            state = await graph.aget_state({"configurable": {"thread_id": thread}})
            humans = [m.content for m in state.values["messages"] if isinstance(m, HumanMessage)]
            assert humans == ["hi", "you there?"]
        finally:
            await saver.adelete_thread(thread)
