"""Memory and preference tools (TOOLS_SPEC section 4, Memory & preferences; ARCHITECTURE 8)."""

from pydantic import Field, model_validator

from sentry_ai.db.enums import MemoryKind
from sentry_ai.db.repositories import MemoryRepo, PreferenceRepo
from sentry_ai.db.schemas import MemoryCreate, MemoryDTO
from sentry_ai.services.preferences import PREFERENCE_SCHEMAS, validate_preference
from sentry_ai.services.schedules import SCHEDULE_KEYS
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, fail, ok, tool

# cosine distance; beyond this a "forget X" query is too far from any memory to guess
FORGET_MAX_DISTANCE = 0.5


class RememberArgs(ToolArgs):
    content: str = Field(
        min_length=1,
        max_length=1000,
        description='one sentence, e.g. "Prefers deep work after 9pm"',
    )
    kind: MemoryKind = "fact"


@tool(ToolTier.WRITE, RememberArgs)
async def remember(args: RememberArgs, ctx: ToolContext) -> ToolResult:
    """Store something the user explicitly asks you to remember ("remember that ...").

    For settings the planner enforces (wake time, quiet hours, no tasks before 7) use
    set_preference instead.
    """
    [vector] = await ctx.embed([args.content])
    memory = await MemoryRepo(ctx.session, ctx.user_id).add(
        MemoryCreate(content=args.content, kind=args.kind, embedding=vector, source="explicit")
    )
    return ok(f"Remembered: {memory.content}", memory_id=memory.id)


class RecallArgs(ToolArgs):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=5, ge=1, le=20)


@tool(ToolTier.READ, RecallArgs)
async def recall(args: RecallArgs, ctx: ToolContext) -> ToolResult:
    """Search what SENTRY remembers about the user, closest meaning first."""
    [vector] = await ctx.embed([args.query])
    matches = await MemoryRepo(ctx.session, ctx.user_id).search(vector, k=args.k)
    found = [
        {"id": m.memory.id, "content": m.memory.content, "kind": m.memory.kind,
         "distance": round(m.distance, 3)}
        for m in matches
    ]  # fmt: skip
    return ok(f"{len(found)} memory(ies) found.", memories=found)


class SetPreferenceArgs(ToolArgs):
    key: str = Field(json_schema_extra={"enum": [*sorted(PREFERENCE_SCHEMAS)]})
    value: str | int | dict[str, str | int] = Field(
        description='e.g. "07:00", 15, {"start": "23:30", "end": "06:30"} or '
        '{"day": "SU", "time": "20:00"}'
    )


@tool(ToolTier.WRITE, SetPreferenceArgs)
async def set_preference(args: SetPreferenceArgs, ctx: ToolContext) -> ToolResult:
    """Save a structured preference the planner enforces in code (DATA_MODEL section 7).

    E.g. "no tasks before 7" -> no_tasks_before "07:00". Windows (quiet_hours,
    deep_work_window) take an object {"start": "HH:MM", "end": "HH:MM"}. If the user gives
    only one end of a window, ask for the other; never guess it. Free-form facts go to remember.
    """
    try:
        value = validate_preference(args.key, args.value)
    except ValueError as exc:
        return fail("invalid_args", str(exc))
    prefs = PreferenceRepo(ctx.session, ctx.user_id)
    await prefs.set(args.key, value)
    message = f"Set {args.key} to {value}."
    if args.key in SCHEDULE_KEYS:  # e.g. a new wake_time moves the morning brief right away
        moved = await ctx.daily.apply(await prefs.all())
        if moved:
            message += f" Rescheduled: {', '.join(j.replace('_', ' ') for j in moved)}."
    return ok(message, key=args.key, value=value)


class NoArgs(ToolArgs):
    pass


@tool(ToolTier.READ, NoArgs)
async def get_preferences(args: NoArgs, ctx: ToolContext) -> ToolResult:
    """All saved preferences (wake/sleep time, quiet hours, deep-work window, ...)."""
    prefs = await PreferenceRepo(ctx.session, ctx.user_id).all()
    return ok(f"{len(prefs)} preference(s) set.", preferences=dict(sorted(prefs.items())))


class ForgetArgs(ToolArgs):
    memory_id: int | None = None
    query: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _one_target(self) -> "ForgetArgs":
        if (self.memory_id is None) == (self.query is None):
            raise ValueError("give exactly one of memory_id or query")
        return self


async def _target(args: ForgetArgs, ctx: ToolContext) -> MemoryDTO | ToolResult:
    memories = MemoryRepo(ctx.session, ctx.user_id)
    if args.memory_id is not None:
        memory = await memories.get(args.memory_id)
        if memory is None:
            return fail("not_found", f"There is no memory {args.memory_id}.")
        return memory
    [vector] = await ctx.embed([args.query or ""])
    matches = await memories.search(vector, k=1)
    if not matches or matches[0].distance > FORGET_MAX_DISTANCE:
        return fail("not_found", f"Nothing I remember matches {args.query!r}.")
    return matches[0].memory


async def _forget_preview(args: ForgetArgs, ctx: ToolContext) -> str | ToolResult:
    target = await _target(args, ctx)
    if isinstance(target, ToolResult):
        return target
    return f"This will permanently forget memory {target.id}: '{target.content}'."


@tool(ToolTier.DESTRUCTIVE, ForgetArgs, preview=_forget_preview)
async def forget(args: ForgetArgs, ctx: ToolContext) -> ToolResult:
    """Permanently delete one memory, by id or by the closest match to `query`.

    Call it straight away: SENTRY shows the user what will happen and asks them to confirm
    before anything changes. Never ask "shall I?" yourself first.
    """
    target = await _target(args, ctx)
    if isinstance(target, ToolResult):
        return target
    await MemoryRepo(ctx.session, ctx.user_id).delete(target.id)
    return ok(f"Forgot: '{target.content}'.", memory_id=target.id)
