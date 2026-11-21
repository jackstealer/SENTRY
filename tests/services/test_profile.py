from datetime import date, time
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sentry_ai.config import Settings
from sentry_ai.db.models import Habit, Preference, Routine
from sentry_ai.db.repositories import HabitRepo, RoutineRepo
from sentry_ai.db.schemas import RoutineCreate, RoutineUpdate
from sentry_ai.services.profile import ProfileError, load_profile, seed_profile

REAL_PROFILE = Path(__file__).resolve().parents[2] / "docs" / "USER_PROFILE.md"

PROFILE = """
# notes above the block are ignored

```yaml
user:
  name: "Atul"
  timezone: "Asia/Kolkata"
  honorific: "sir"
preferences:
  wake_time: "06:30"
  quiet_hours: { start: "23:30", end: "06:30" }
  default_reminder_min: 15
routines:
  - title: "College classes"
    category: study
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    start_time: "09:00"
    duration_min: 300
    remind_before_min: 30
  - title: "Gym"
    category: fitness
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"
    start_time: "18:00"
    duration_min: 90
habits:
  - name: "DSA practice"
    rrule: "FREQ=DAILY"
    preferred_time: "21:00"
  - name: "Workout"
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"
    target_per_period: 5
    period: week
activity:
  store_titles: false
```

## Things SENTRY should never do
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "USER_PROFILE.md"
    path.write_text(text, encoding="utf-8")
    return path


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "user_tz": "Asia/Kolkata",
        "persona_honorific": "sir",
        "user_name": "",
    }
    return Settings(_env_file=None, **(base | overrides))  # type: ignore[call-arg, arg-type]


async def table_counts(session: AsyncSession, user_id: str) -> tuple[int, ...]:
    counts = []
    for model in (Preference, Routine, Habit):
        stmt = select(func.count()).select_from(model).where(model.user_id == user_id)
        counts.append(int(await session.scalar(stmt) or 0))
    return tuple(counts)


# --- parsing --------------------------------------------------------------------------


def test_the_real_profile_parses_and_is_flagged_as_a_template() -> None:
    profile = load_profile(REAL_PROFILE)
    assert profile.is_template
    assert [r.title for r in profile.routines] == ["College classes", "Gym", "Lunch"]
    assert [h.name for h in profile.habits] == ["DSA practice", "Workout", "Reading (20 min)"]
    assert profile.preferences["wake_time"] == {"time": "06:30"}
    assert profile.preferences["max_tasks_per_day"] == {"count": 5}
    assert len(profile.preferences) == 9


def test_fixture_profile_parses_with_typed_values(tmp_path: Path) -> None:
    profile = load_profile(write(tmp_path, PROFILE))
    assert not profile.is_template
    classes = profile.routines[0]
    assert (classes.start_time, classes.rrule, classes.remind_before_min) == (
        time(9, 0),
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        30,
    )
    assert profile.habits[0].preferred_time == time(21, 0)
    assert profile.habits[1].period == "week"


@pytest.mark.parametrize(
    ("find", "replace", "message"),
    [
        ('start_time: "18:00"', "start_time: 18:00", "quoted strings"),  # YAML -> 1080
        ("duration_min: 90", "duration_min: 0", "duration_min"),
        ("category: fitness", "category: napping", "category"),
        # first occurrence is the Gym routine (routines come before habits)
        ('rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"', 'rrule: "FREQ=SOMETIMES"', "rrule"),
    ],
)
def test_broken_items_name_the_problem(
    tmp_path: Path, find: str, replace: str, message: str
) -> None:
    assert find in PROFILE
    with pytest.raises(ProfileError, match=message):
        load_profile(write(tmp_path, PROFILE.replace(find, replace, 1)))


def test_typos_and_duplicates_are_errors(tmp_path: Path) -> None:
    typo = PROFILE.replace("remind_before_min: 30", "remind_befor_min: 30")
    with pytest.raises(ProfileError, match="remind_befor_min"):
        load_profile(write(tmp_path, typo))
    dupe = PROFILE.replace('title: "Gym"', 'title: "college CLASSES"')
    with pytest.raises(ProfileError, match="duplicate routine"):
        load_profile(write(tmp_path, dupe))


def test_missing_or_malformed_yaml(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="cannot read"):
        load_profile(tmp_path / "missing.md")
    with pytest.raises(ProfileError, match="no ```yaml block"):
        load_profile(write(tmp_path, "# just notes\n"))
    with pytest.raises(ProfileError, match="invalid YAML"):
        load_profile(write(tmp_path, "```yaml\nroutines: [unclosed\n```\n"))
    with pytest.raises(ProfileError, match="unknown preference"):
        load_profile(write(tmp_path, '```yaml\npreferences:\n  bedtime: "23:00"\n```\n'))


# --- seeding --------------------------------------------------------------------------


async def test_seed_creates_everything_once(
    tmp_path: Path, session: AsyncSession, user_id: str
) -> None:
    profile = load_profile(write(tmp_path, PROFILE))
    report = await seed_profile(session, user_id, profile, settings=settings())
    assert report.created == {"preferences": 3, "routines": 2, "habits": 2}
    assert report.warnings == []
    assert await table_counts(session, user_id) == (3, 2, 2)

    gym = await RoutineRepo(session, user_id).get_by_title("Gym")
    assert gym is not None and (gym.start_time, gym.duration_min, gym.is_fixed) == (
        time(18, 0),
        90,
        True,
    )


async def test_seed_is_safe_to_run_twice(
    tmp_path: Path, session: AsyncSession, user_id: str
) -> None:
    profile = load_profile(write(tmp_path, PROFILE))
    await seed_profile(session, user_id, profile, settings=settings())
    again = await seed_profile(session, user_id, profile, settings=settings())
    assert again.is_noop
    assert again.unchanged == {"preferences": 3, "routines": 2, "habits": 2}
    assert await table_counts(session, user_id) == (3, 2, 2)  # no duplicates
    assert "0 created, 0 updated, 2 unchanged" in again.summary()


async def test_editing_the_profile_updates_only_what_changed(
    tmp_path: Path, session: AsyncSession, user_id: str
) -> None:
    await seed_profile(
        session, user_id, load_profile(write(tmp_path, PROFILE)), settings=settings()
    )
    routines = RoutineRepo(session, user_id)
    gym = await routines.get_by_title("Gym")
    assert gym is not None
    # a later change made through chat, on a field the YAML does not mention
    await routines.update(gym.id, RoutineUpdate(active_from=date(2026, 12, 1)))

    edited = PROFILE.replace('start_time: "18:00"', 'start_time: "19:00"').replace(
        'wake_time: "06:30"', 'wake_time: "07:00"'
    )
    report = await seed_profile(
        session, user_id, load_profile(write(tmp_path, edited)), settings=settings()
    )
    assert report.updated == {"preferences": 1, "routines": 1}
    moved = await routines.get(gym.id)
    assert moved is not None
    assert (moved.start_time, moved.active_from) == (time(19, 0), date(2026, 12, 1))


async def test_rows_missing_from_the_profile_are_kept_with_a_warning(
    tmp_path: Path, session: AsyncSession, user_id: str
) -> None:
    await RoutineRepo(session, user_id).create(
        RoutineCreate(
            title="Guitar",
            category="personal",
            rrule="FREQ=DAILY",
            start_time=time(20),
            duration_min=30,
        )
    )
    report = await seed_profile(
        session, user_id, load_profile(write(tmp_path, PROFILE)), settings=settings()
    )
    assert "routine 'Guitar' is in the DB but not in the profile" in report.warnings
    assert await RoutineRepo(session, user_id).get_by_title("Guitar") is not None
    assert await HabitRepo(session, user_id).get_by_name("Workout") is not None


async def test_env_wins_over_the_yaml_user_block(
    tmp_path: Path, session: AsyncSession, user_id: str
) -> None:
    profile = load_profile(write(tmp_path, PROFILE))
    report = await seed_profile(
        session, user_id, profile, settings=settings(user_tz="Europe/London", user_name="Someone")
    )
    assert any("USER_TZ='Europe/London'; .env is used" in w for w in report.warnings)
    assert any("USER_NAME='Someone'" in w for w in report.warnings)
