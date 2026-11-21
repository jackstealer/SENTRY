from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sentry_ai.db.models import Preference
from sentry_ai.db.repositories.base import Repo
from sentry_ai.db.schemas import PreferenceDTO


class PreferenceRepo(Repo):
    """Stores values as given; validating known keys is the caller's job (services/preferences)."""

    async def get(self, key: str) -> PreferenceDTO | None:
        stmt = sa.select(Preference).where(
            Preference.user_id == self.user_id, Preference.key == key
        )
        return await self._one(stmt, PreferenceDTO)

    async def all(self) -> dict[str, dict[str, Any]]:
        stmt = sa.select(Preference).where(Preference.user_id == self.user_id)
        return {p.key: p.value for p in await self._many(stmt, PreferenceDTO)}

    async def set(self, key: str, value: dict[str, Any]) -> PreferenceDTO:
        stmt = pg_insert(Preference).values(user_id=self.user_id, key=key, value=value)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Preference.user_id, Preference.key],
            set_={"value": stmt.excluded.value, "updated_at": sa.func.now()},
        )
        return await self._created(stmt.returning(Preference), PreferenceDTO)

    async def delete(self, key: str) -> bool:
        stmt = sa.delete(Preference).where(
            Preference.user_id == self.user_id, Preference.key == key
        )
        return await self._affected(stmt.returning(Preference.key))
