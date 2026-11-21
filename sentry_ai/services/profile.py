"""Read the YAML block in docs/USER_PROFILE.md and seed routines, habits and preferences.

Seeding is idempotent and additive: rows are matched by title/name (case-insensitive),
created or updated, and never deleted. Only fields written in the YAML are compared, so a
value changed later through chat (e.g. a routine's active window) is not reset.
"""

import re
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.config import Settings
from sentry_ai.db.repositories import HabitRepo, PreferenceRepo, RoutineRepo
from sentry_ai.db.schemas import HabitCreate, HabitUpdate, RoutineCreate, RoutineUpdate
from sentry_ai.services.preferences import validate_preference
from sentry_ai.services.time import parse_hhmm

TEMPLATE_NAME = "Your Name"
_YAML_BLOCK = re.compile(r"^```ya?ml[ \t]*\r?\n(.*?)^```", re.MULTILINE | re.DOTALL)
T = TypeVar("T", bound=BaseModel)
_TIME_HINT = 'times must be quoted strings like "18:00" (unquoted, YAML reads 18:00 as 1080)'


class ProfileError(ValueError):
    """The profile cannot be used as-is; the message says what to fix."""


def _hhmm(value: object) -> object:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(_TIME_HINT)
    return parse_hhmm(value)


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in a key is an error, not ignored


class ProfileRoutine(_Item):
    title: str
    category: str
    rrule: str
    start_time: Any
    duration_min: int
    is_fixed: bool = True
    remind_before_min: int | None = None

    _time = field_validator("start_time", mode="before")(_hhmm)


class ProfileHabit(_Item):
    name: str
    rrule: str
    target_per_period: int = 1
    period: str = "day"
    preferred_time: Any = None
    remind_before_min: int | None = None

    _time = field_validator("preferred_time", mode="before")(_hhmm)


class ProfileUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    timezone: str | None = None
    honorific: str | None = None


class Profile(BaseModel):
    # later-phase sections (activity, desktop, voice, mail, projects) are ignored here
    model_config = ConfigDict(extra="ignore")

    user: ProfileUser = ProfileUser()
    preferences: dict[str, dict[str, Any]] = Field(default_factory=dict)
    routines: list[RoutineCreate] = Field(default_factory=list)
    habits: list[HabitCreate] = Field(default_factory=list)

    @field_validator("preferences", mode="before")
    @classmethod
    def _canonical_preferences(cls, raw: object) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            raise ValueError("preferences must be a mapping")
        return {str(key): validate_preference(str(key), value) for key, value in raw.items()}

    @field_validator("routines", mode="before")
    @classmethod
    def _routines(cls, raw: object) -> list[RoutineCreate]:
        return [_build(RoutineCreate, ProfileRoutine, item, "title") for item in _as_list(raw)]

    @field_validator("habits", mode="before")
    @classmethod
    def _habits(cls, raw: object) -> list[HabitCreate]:
        return [_build(HabitCreate, ProfileHabit, item, "name") for item in _as_list(raw)]

    @model_validator(mode="after")
    def _unique_names(self) -> "Profile":
        for label, names in (
            ("routine", [r.title for r in self.routines]),
            ("habit", [h.name for h in self.habits]),
        ):
            lowered = [n.lower() for n in names]
            dupes = sorted({n for n in names if lowered.count(n.lower()) > 1})
            if dupes:
                raise ValueError(f"duplicate {label} names: {', '.join(dupes)}")
        return self

    @property
    def is_template(self) -> bool:
        return self.user.name.strip() == TEMPLATE_NAME


def _as_list(raw: object) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("must be a list")
    return raw


def _build(target: type[T], shape: type[_Item], item: object, key: str) -> T:
    """Validate a YAML item, then build the DTO from only the fields the YAML set."""
    label = item.get(key, "?") if isinstance(item, dict) else "?"
    try:
        parsed = shape.model_validate(item)
        return target.model_validate(parsed.model_dump(exclude_unset=True))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"{label!r}: {problems}") from exc


def load_profile(path: Path) -> Profile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError(f"cannot read {path}: {exc.strerror}") from exc
    match = _YAML_BLOCK.search(text)
    if match is None:
        raise ProfileError(f"{path}: no ```yaml block found")
    try:
        data = yaml.safe_load(match[1]) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: the YAML block must be a mapping")
    try:
        return Profile.model_validate(data)
    except ValidationError as exc:
        problems = "\n".join(f"  {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ProfileError(f"{path}: invalid profile\n{problems}") from exc


class SeedReport(BaseModel):
    created: dict[str, int] = Field(default_factory=dict)
    updated: dict[str, int] = Field(default_factory=dict)
    unchanged: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def count(self, kind: str, outcome: str) -> None:
        bucket: dict[str, int] = getattr(self, outcome)
        bucket[kind] = bucket.get(kind, 0) + 1

    @property
    def is_noop(self) -> bool:
        return not any(self.created.values()) and not any(self.updated.values())

    def summary(self) -> str:
        lines = [
            f"{kind}: {self.created.get(kind, 0)} created, {self.updated.get(kind, 0)} updated, "
            f"{self.unchanged.get(kind, 0)} unchanged"
            for kind in ("preferences", "routines", "habits")
        ]
        return "\n".join(lines + [f"warning: {w}" for w in self.warnings])


async def seed_profile(
    session: AsyncSession, user_id: str, profile: Profile, *, settings: Settings
) -> SeedReport:
    """Upsert the profile into the DB inside the caller's transaction."""
    report = SeedReport(warnings=_config_mismatches(profile, settings))

    prefs = PreferenceRepo(session, user_id)
    current = await prefs.all()
    for key, value in profile.preferences.items():
        if key not in current:
            outcome = "created"
        elif current[key] != value:
            outcome = "updated"
        else:
            report.count("preferences", "unchanged")
            continue
        await prefs.set(key, value)
        report.count("preferences", outcome)

    routines = RoutineRepo(session, user_id)
    for routine in profile.routines:
        existing = await routines.get_by_title(routine.title)
        if existing is None:
            await routines.create(routine)
            report.count("routines", "created")
            continue
        changes = _diff(routine, existing)
        if changes:
            await routines.update(existing.id, RoutineUpdate.model_validate(changes))
        report.count("routines", "updated" if changes else "unchanged")

    habits = HabitRepo(session, user_id)
    for habit in profile.habits:
        found = await habits.get_by_name(habit.name)
        if found is None:
            await habits.create(habit)
            report.count("habits", "created")
            continue
        changes = _diff(habit, found)
        if changes:
            await habits.update(found.id, HabitUpdate.model_validate(changes))
        report.count("habits", "updated" if changes else "unchanged")

    wanted = {r.title.lower() for r in profile.routines}
    for row in await routines.list_active():
        if row.title.lower() not in wanted:
            report.warnings.append(f"routine {row.title!r} is in the DB but not in the profile")
    wanted = {h.name.lower() for h in profile.habits}
    for found in await habits.list_active():
        if found.name.lower() not in wanted:
            report.warnings.append(f"habit {found.name!r} is in the DB but not in the profile")
    return report


def _diff(wanted: BaseModel, existing: BaseModel) -> dict[str, Any]:
    """Fields the YAML set whose values differ from the stored row (title/name excluded)."""
    return {
        field: getattr(wanted, field)
        for field in wanted.model_fields_set - {"title", "name"}
        if getattr(wanted, field) != getattr(existing, field)
    }


def _config_mismatches(profile: Profile, settings: Settings) -> list[str]:
    """.env wins (hard rule 1); a different value in the YAML only produces a warning."""
    checks = [
        ("timezone", profile.user.timezone, settings.user_tz, "USER_TZ"),
        ("honorific", profile.user.honorific, settings.persona_honorific, "PERSONA_HONORIFIC"),
    ]
    if settings.user_name:
        checks.append(("name", profile.user.name, settings.user_name, "USER_NAME"))
    return [
        f"profile {label} {yaml_value!r} differs from {env_name}={env_value!r}; .env is used"
        for label, yaml_value, env_value, env_name in checks
        if yaml_value is not None and yaml_value != env_value
    ]
