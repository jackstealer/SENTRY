from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sentry_ai.db.models import CalendarEvent
from sentry_ai.db.repositories.base import Repo
from sentry_ai.db.schemas import CalendarEventDTO, CalendarEventUpsert

_SYNCED_FIELDS = ("title", "start_at", "end_at", "all_day", "is_sentry_owned", "synced_at")


class CalendarRepo(Repo):
    async def upsert_events(self, events: Sequence[CalendarEventUpsert]) -> int:
        """Insert or refresh cached events keyed by (calendar_id, gcal_event_id)."""
        if not events:
            return 0
        stmt = pg_insert(CalendarEvent).values(
            [event.model_dump() | {"user_id": self.user_id} for event in events]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                CalendarEvent.user_id,
                CalendarEvent.calendar_id,
                CalendarEvent.gcal_event_id,
            ],
            set_={field: stmt.excluded[field] for field in _SYNCED_FIELDS},
        )
        await self.session.execute(stmt)
        return len(events)

    async def between(self, start: datetime, end: datetime) -> list[CalendarEventDTO]:
        """Events overlapping [start, end), earliest first."""
        stmt = (
            sa.select(CalendarEvent)
            .where(
                CalendarEvent.user_id == self.user_id,
                CalendarEvent.start_at < end,
                CalendarEvent.end_at > start,
            )
            .order_by(CalendarEvent.start_at, CalendarEvent.id)
        )
        return await self._many(stmt, CalendarEventDTO)

    async def delete(self, calendar_id: str, gcal_event_id: str) -> bool:
        stmt = sa.delete(CalendarEvent).where(
            CalendarEvent.user_id == self.user_id,
            CalendarEvent.calendar_id == calendar_id,
            CalendarEvent.gcal_event_id == gcal_event_id,
        )
        return await self._affected(stmt.returning(CalendarEvent.id))
