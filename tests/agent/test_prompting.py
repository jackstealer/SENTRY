import re
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from sentry_ai.agent.prompting import render

PERSONA = Path(__file__).resolve().parents[2] / "docs" / "PERSONA.md"
SYSTEM_VALUES = {
    "user_name": "Atul",
    "now_local": "11:50 PM",
    "tz": "Asia/Kolkata",
    "weekday": "Sunday",
    "date": "22 Nov 2026",
    "honorific": "sir",
    "preferences_block": "- no_tasks_before: 07:00",
    "memories_block": "- <external>Prefers deep work after 9pm</external>",
    "today_snapshot": "Tasks due: none",
}


def test_system_prompt_fills_every_section() -> None:
    text = render("system", **SYSTEM_VALUES)
    assert text.startswith("You are SENTRY, a personal routine assistant for Atul.")
    assert "Current time: 11:50 PM (Asia/Kolkata). Today is Sunday, 22 Nov 2026." in text
    assert 'Occasionally address the user as "sir".' in text
    assert "- no_tasks_before: 07:00" in text
    assert text.endswith("Today snapshot:\nTasks due: none")
    assert "{{" not in text and "{%" not in text


def test_blank_honorific_drops_that_sentence() -> None:
    text = render("system", **(SYSTEM_VALUES | {"honorific": ""}))
    assert "address the user" not in text
    assert "- Composed, precise, lightly dry humour.\n" in text


def test_a_missing_value_is_an_error_not_an_empty_string() -> None:
    values = dict(SYSTEM_VALUES)
    del values["today_snapshot"]
    with pytest.raises(UndefinedError):
        render("system", **values)


def test_route_prompt_with_and_without_a_pending_confirmation() -> None:
    plain = render("route", pending=None)
    assert 'always "none"' in plain and "manage:" in plain
    pending = render("route", pending="This will archive 4 completed task(s).")
    assert 'A confirmation is pending: "This will archive 4 completed task(s)."' in pending


def test_system_prompt_rules_match_persona_section_3() -> None:
    """PERSONA.md is the source of truth for the system prompt's Style and Rules lines."""
    persona = PERSONA.read_text(encoding="utf-8")
    block = persona.split("## 3. System prompt (template)", 1)[1].split("```")[1]
    rendered = render("system", **SYSTEM_VALUES)
    for line in block.splitlines():
        if line.startswith("- ") and "{" not in line:
            assert line in rendered, line
    assert re.search(r"^Rules:$", rendered, re.MULTILINE)
