"""Calendar tools. Phase 2 reads the local cache only; Google sync and SENTRY-calendar writes
(create_calendar_block, delete_calendar_block) arrive with Phase 6."""

from datetime import timedelta

from pydantic import AwareDatetime, Field, model_validator

from sentry_ai.db.repositories import CalendarRepo
from sentry_ai.tools.base import ToolArgs, ToolContext, ToolResult, ToolTier, ok, tool
from sentry_ai.tools.tasks import WHEN

MAX_RANGE = timedelta(days=31)


class RangeArgs(ToolArgs):
    start: AwareDatetime = Field(description=WHEN)
    end: AwareDatetime = Field(description=WHEN)

    @model_validator(mode="after")
    def _sane(self) -> "RangeArgs":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.end - self.start > MAX_RANGE:
            raise ValueError("ask for at most 31 days at a time")
        return self


@tool(ToolTier.READ, RangeArgs)
async def get_calendar_events(args: RangeArgs, ctx: ToolContext) -> ToolResult:
    """Calendar events overlapping [start, end), from SENTRY's synced copy of the calendar."""
    events = await CalendarRepo(ctx.session, ctx.user_id).between(args.start, args.end)
    fields = {"id", "title", "start_at", "end_at", "all_day", "calendar_id"}
    return ok(
        f"{len(events)} event(s)." if events else "No calendar events in that range.",
        events=[e.model_dump(include=fields) for e in events],
    )
