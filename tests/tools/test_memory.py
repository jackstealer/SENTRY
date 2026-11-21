import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.agent.llm import LLMNotConfiguredError
from sentry_ai.db.repositories import MemoryRepo, PreferenceRepo
from sentry_ai.db.schemas import MemoryCreate
from sentry_ai.tools.executor import execute


async def remembered(call, content, kind="fact"):
    result = await call("remember", content=content, kind=kind)
    assert result.ok, result.message
    return result.data["memory_id"]


# --- remember -----------------------------------------------------------------------------


async def test_remember_stores_an_explicit_embedded_memory(call, session, user_id, embedder):
    memory_id = await remembered(call, "Prefers deep work after 9pm", kind="preference")
    memory = await MemoryRepo(session, user_id).get(memory_id)
    assert memory is not None
    assert (memory.content, memory.kind, memory.source) == (
        "Prefers deep work after 9pm",
        "preference",
        "explicit",
    )
    assert embedder.calls == [["Prefers deep work after 9pm"]]


async def test_remember_rejects_bad_input(call, embedder):
    assert (await call("remember", content="")).error == "invalid_args"
    assert (await call("remember", content="x", kind="secret")).error == "invalid_args"
    assert (await call("remember", content="x" * 1001)).error == "invalid_args"
    assert embedder.calls == []


async def test_remember_without_an_embedding_model_says_what_is_missing(make_ctx, session, user_id):
    async def unconfigured(texts: list[str]) -> list[list[float]]:
        raise LLMNotConfiguredError("OPENAI_EMBEDDING_MODEL is not set")

    ctx = make_ctx().model_copy(update={"embed": unconfigured})
    result = await execute("remember", {"content": "Likes chai"}, ctx)
    assert (result.ok, result.error) == (False, "not_configured")
    assert "OPENAI_EMBEDDING_MODEL" in result.message
    assert await MemoryRepo(session, user_id).search([1.0] * 1536) == []


# --- recall -------------------------------------------------------------------------------


async def test_recall_returns_closest_first(call):
    chai = await remembered(call, "Drinks chai every morning")
    await remembered(call, "Has a dog named Bruno")
    result = await call("recall", query="morning chai", k=2)
    assert result.ok
    assert result.data["memories"][0]["id"] == chai
    assert len(result.data["memories"]) == 2


async def test_recall_rejects_bad_input(call):
    assert (await call("recall", query="")).error == "invalid_args"
    assert (await call("recall", query="x", k=0)).error == "invalid_args"
    assert (await call("recall", query="x", k=21)).error == "invalid_args"


async def test_recall_ignores_other_users_and_superseded(call, session, embedder):
    old = await remembered(call, "Gym at 6pm")
    new = await remembered(call, "Gym at 7pm")
    await MemoryRepo(session, "test-user").supersede(old, new)
    await MemoryRepo(session, "someone-else").add(
        MemoryCreate(content="Gym at 6pm", kind="fact", embedding=embedder.vector("Gym at 6pm"),
                     source="explicit")
    )  # fmt: skip
    result = await call("recall", query="Gym at 6pm")
    assert [m["id"] for m in result.data["memories"]] == [new]


# --- set_preference / get_preferences -----------------------------------------------------


async def test_set_preference_stores_the_canonical_value(call, session, user_id):
    result = await call("set_preference", key="no_tasks_before", value="07:00")
    assert result.ok and result.data["value"] == {"time": "07:00"}
    stored = await PreferenceRepo(session, user_id).get("no_tasks_before")
    assert stored is not None and stored.value == {"time": "07:00"}


async def test_set_preference_rejects_bad_input(call, session, user_id):
    unknown = await call("set_preference", key="bedtime", value="23:00")
    assert unknown.error == "invalid_args" and "known keys" in unknown.message
    assert (await call("set_preference", key="wake_time", value="7am")).error == "invalid_args"
    assert (await call("set_preference", key="max_tasks_per_day", value=0)).error == "invalid_args"
    assert (await call("set_preference", key="wake_time")).error == "invalid_args"
    assert await PreferenceRepo(session, user_id).all() == {}


async def test_set_preference_quiet_hours_may_wrap_midnight(call, session, user_id):
    value = {"start": "23:30", "end": "06:30"}
    assert (await call("set_preference", key="quiet_hours", value=value)).ok
    stored = await PreferenceRepo(session, user_id).get("quiet_hours")
    assert stored is not None and stored.value == value
    same = await call("set_preference", key="quiet_hours", value={"start": "23:30", "end": "23:30"})
    assert same.error == "invalid_args"


async def test_get_preferences_lists_mine_sorted(call, session):
    await call("set_preference", key="wake_time", value="06:30")
    await call("set_preference", key="default_reminder_min", value=15)
    await PreferenceRepo(session, "someone-else").set("sleep_time", {"time": "01:00"})
    result = await call("get_preferences")
    assert result.data["preferences"] == {
        "default_reminder_min": {"minutes": 15},
        "wake_time": {"time": "06:30"},
    }


async def test_get_preferences_when_empty_and_with_bad_input(call):
    assert (await call("get_preferences")).data == {"preferences": {}}
    assert (await call("get_preferences", key="x")).error == "invalid_args"


# --- forget (destructive) -----------------------------------------------------------------


async def test_forget_refuses_without_confirmation(call, session, user_id):
    memory_id = await remembered(call, "Has a dog named Bruno")
    result = await call("forget", memory_id=memory_id)
    assert (result.ok, result.error) == (False, "confirmation_required")
    assert result.data["pending"]["summary"] == (
        f"This will permanently forget memory {memory_id}: 'Has a dog named Bruno'."
    )
    assert await MemoryRepo(session, user_id).get(memory_id) is not None


@pytest.mark.parametrize("by", ["memory_id", "query"])
async def test_forget_once_confirmed_deletes_it(call, session, user_id, by):
    memory_id = await remembered(call, "Has a dog named Bruno")
    keep = await remembered(call, "Drinks chai every morning")
    args = {"memory_id": memory_id} if by == "memory_id" else {"query": "dog named Bruno"}
    result = await call("forget", confirmed=True, **args)
    assert result.ok and result.data["memory_id"] == memory_id
    memories = MemoryRepo(session, user_id)
    assert await memories.get(memory_id) is None
    assert await memories.get(keep) is not None


async def test_forget_rejects_bad_input_and_far_matches(call):
    assert (await call("forget")).error == "invalid_args"
    assert (await call("forget", memory_id=1, query="x")).error == "invalid_args"
    await remembered(call, "Has a dog named Bruno")
    far = await call("forget", query="quantum chromodynamics")
    assert far.error == "not_found"  # nothing to confirm, so no confirmation prompt
    assert (await call("forget", memory_id=999_999)).error == "not_found"


async def test_forget_by_id_ignores_other_users_memories(call, session: AsyncSession, embedder):
    theirs = await MemoryRepo(session, "someone-else").add(
        MemoryCreate(content="secret", kind="fact", embedding=embedder.vector("secret"),
                     source="explicit")
    )  # fmt: skip
    result = await call("forget", memory_id=theirs.id, confirmed=True)
    assert result.error == "not_found"
    assert await MemoryRepo(session, "someone-else").get(theirs.id) is not None


async def test_get_preferences_shows_the_latest_value_after_an_update(call):
    await call("set_preference", key="quiet_hours", value={"start": "23:30", "end": "06:30"})
    await call("set_preference", key="quiet_hours", value={"start": "00:00", "end": "07:00"})
    result = await call("get_preferences")
    assert result.data["preferences"] == {"quiet_hours": {"start": "00:00", "end": "07:00"}}
    assert result.message == "1 preference(s) set."
