from typing import Any

import pytest

from sentry_ai.services.preferences import (
    PREFERENCE_SCHEMAS,
    UnknownPreferenceError,
    validate_preference,
)


@pytest.mark.parametrize(
    ("key", "raw", "canonical"),
    [
        ("wake_time", "06:30", {"time": "06:30"}),
        ("no_tasks_before", {"time": "07:00"}, {"time": "07:00"}),
        ("default_reminder_min", 15, {"minutes": 15}),
        ("max_tasks_per_day", 5, {"count": 5}),
        ("weekly_review", {"day": "SU", "time": "20:00"}, {"day": "SU", "time": "20:00"}),
        ("quiet_hours", {"start": "23:30", "end": "06:30"}, {"start": "23:30", "end": "06:30"}),
    ],
)
def test_short_and_full_forms_become_canonical(key: str, raw: Any, canonical: Any) -> None:
    assert validate_preference(key, raw) == canonical


def test_every_known_key_from_data_model_is_covered() -> None:
    assert set(PREFERENCE_SCHEMAS) == {
        "wake_time",
        "sleep_time",
        "evening_review_time",
        "weekly_review",
        "no_tasks_before",
        "deep_work_window",
        "default_reminder_min",
        "quiet_hours",
        "max_tasks_per_day",
    }


def test_unknown_key_is_rejected_with_the_known_list() -> None:
    with pytest.raises(UnknownPreferenceError, match="known keys: deep_work_window"):
        validate_preference("bedtime", "23:00")


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("wake_time", "6:30"),
        ("wake_time", "25:00"),
        ("weekly_review", {"day": "Sunday", "time": "20:00"}),
        ("quiet_hours", {"start": "23:30", "end": "23:30"}),
        ("quiet_hours", "23:30-06:30"),  # multi-field shape needs an object
        ("default_reminder_min", -5),
        ("max_tasks_per_day", "5"),  # strict: no string-to-int coercion
        ("wake_time", {"time": "06:30", "extra": True}),
    ],
)
def test_invalid_values_are_rejected(key: str, raw: Any) -> None:
    with pytest.raises(ValueError, match=key):
        validate_preference(key, raw)


def test_unquoted_yaml_time_gets_a_quoting_hint() -> None:
    with pytest.raises(ValueError, match="quote times"):
        validate_preference("evening_review_time", 1320)  # YAML read 22:00 as 1320
