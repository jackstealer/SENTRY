"""`load_context`: today's snapshot, preferences and relevant memories for the system prompt.

Everything here is capped so the system prompt stays within its budget (about 2,000 tokens):
at most MAX_ITEMS per list, MEMORY_CHARS per memory, MEMORY_K memories.
"""

from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from sentry_ai.agent.deps import AgentDeps
from sentry_ai.agent.state import AgentState
from sentry_ai.config import get_settings
from sentry_ai.db.repositories import MemoryRepo, PreferenceRepo
from sentry_ai.observability.logging import get_logger
from sentry_ai.services.time import to_local
from sentry_ai.tools.query import DayArgs, get_today_overview

log = get_logger(__name__)

MAX_ITEMS = 10
MEMORY_K = 6
MEMORY_MAX_DISTANCE = 0.6  # cosine distance; farther memories are noise
MEMORY_CHARS = 200


def clock12(value: str | datetime) -> str:
    """'2026-11-23T21:00+05:30' -> '9:00 PM' (PERSONA: 12-hour local times)."""
    at = to_local(datetime.fromisoformat(value) if isinstance(value, str) else value)
    return f"{at:%I:%M %p}".lstrip("0")


def external(text: str) -> str:
    """Mark text from outside SENTRY (calendar, memories) as data, never instructions."""
    return f"<external>{text}</external>"


def _capped(lines: list[str]) -> list[str]:
    extra = len(lines) - MAX_ITEMS
    return lines[:MAX_ITEMS] + ([f"(+{extra} more)"] if extra > 0 else [])


def _section(title: str, lines: list[str], empty: str = "none") -> str:
    body = "; ".join(_capped(lines)) if lines else empty
    return f"{title}: {body}"


def render_snapshot(overview: dict[str, Any]) -> str:
    """get_today_overview's data as compact lines, with ids so tools can be called directly."""
    fixed = [
        f"{clock12(b['start_at'])}-{clock12(b['end_at'])} {b['title']}"
        for b in overview["fixed_blocks"]
    ]
    events = [
        f"{'all day' if e['all_day'] else clock12(e['start_at']) + '-' + clock12(e['end_at'])} "
        f"{external(e['title'])}"
        for e in overview["calendar_events"]
    ]
    plan = overview["plan"]
    plan_lines = (
        [
            f"{clock12(b['start_at'])} {b['title']} [{b['kind']} block #{b['id']}]"
            for b in plan["blocks"]
        ]
        if plan
        else []
    )
    tasks = []
    for t in overview["tasks_due"]:
        due = f", due {clock12(t['due_at'])} {t['due_at'][:10]}" if t["due_at"] else ""
        tasks.append(f"#{t['id']} {t['title']} (p{t['priority']}{due})")
    habits = [
        f"#{h['habit_id']} {h['name']}"
        + (f" at {clock12(h['preferred_at'])}" if h["preferred_at"] else "")
        + f" ({h['logged'] or 'not logged'})"
        for h in overview["habits_due"]
    ]
    reminders = [
        f"#{r['id']} {clock12(r['fire_at'])} {r['message']}" for r in overview["reminders"]
    ]
    return "\n".join(
        [
            _section("Fixed blocks", fixed),
            _section("Calendar", events),
            _section(f"Confirmed plan #{plan['plan_id']}" if plan else "Plan", plan_lines,
                     empty="no confirmed plan"),
            _section("Tasks due or overdue", tasks),
            _section("Habits due", habits),
            _section("Pending reminders", reminders),
        ]
    )  # fmt: skip


def render_preferences(prefs: dict[str, dict[str, Any]]) -> str:
    def text(value: dict[str, Any]) -> str:
        if {"start", "end"} <= value.keys():
            return f"{value['start']}-{value['end']}"
        if "minutes" in value:
            return f"{value['minutes']} min"
        return " ".join(str(v) for v in value.values())

    return "\n".join(f"- {k}: {text(v)}" for k, v in sorted(prefs.items())) or "- none set"


def render_memories(memories: list[dict[str, Any]]) -> str:
    lines = [f"- {external(m['content'][:MEMORY_CHARS])}" for m in memories]
    return "\n".join(lines) or "- none"


def _last_user_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


async def load_context(state: AgentState, runtime: Runtime[AgentDeps]) -> dict[str, Any]:
    deps, user_id = runtime.context, state["user_id"]
    async with deps.sessions() as session:
        ctx = deps.tool_context(session, user_id=user_id, trace_id=state["trace_id"])
        overview = await get_today_overview.run(DayArgs(), ctx)
        prefs = await PreferenceRepo(session, user_id).all()
        memories = await _memories(_last_user_text(state), ctx.session, deps, user_id)
    settings = get_settings()
    now = to_local(deps.clock())
    context = {
        "user_name": settings.user_name or "the user",
        "honorific": settings.persona_honorific,
        "now_local": clock12(now),
        "tz": settings.user_tz,
        "weekday": f"{now:%A}",
        "date": f"{now:%d %b %Y}".lstrip("0"),
        "preferences_block": render_preferences(prefs),
        "memories_block": render_memories(memories),
        "today_snapshot": render_snapshot(overview.data),
    }
    return {"context": context, "memories": memories, "tool_calls_count": 0}


async def _memories(text: str, session: Any, deps: AgentDeps, user_id: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    try:
        [vector] = await deps.embed([text])
    except Exception as exc:  # memory is a nice-to-have: never block the turn on it
        log.warning("memories_skipped", error_type=type(exc).__name__)
        return []
    repo = MemoryRepo(session, user_id)
    matches = [
        m for m in await repo.search(vector, k=MEMORY_K) if m.distance <= MEMORY_MAX_DISTANCE
    ]
    await repo.touch([m.memory.id for m in matches], deps.clock())
    return [
        {"id": m.memory.id, "content": m.memory.content, "kind": m.memory.kind,
         "distance": round(m.distance, 3)}
        for m in matches
    ]  # fmt: skip
