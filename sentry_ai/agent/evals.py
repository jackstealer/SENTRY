"""Eval cases (tests/evals/agent_cases.yaml) and how a turn is scored. Pure: no DB, no API.

scripts/run_evals.py runs the cases against the real API; tests check this file offline.

Argument matchers in `expect.tools[].args`:
    value                exact; ISO datetimes compare as instants, "19:00" equals "19:00:00"
    {contains: text}     case-insensitive substring
    {any_of: [a, b]}     any one of the values
    {present: true}      the argument is given (not null)
    {ref: tasks.0}       the id of the first task created by `setup`
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from sentry_ai.db.enums import MemoryKind
from sentry_ai.db.schemas import HabitCreate, ReminderCreate, RoutineCreate, TaskCreate
from sentry_ai.services.preferences import validate_preference
from sentry_ai.tools.base import ToolTier
from sentry_ai.tools.registry import TOOLS, Intent

CASES_FILE = Path(__file__).resolve().parents[2] / "tests" / "evals" / "agent_cases.yaml"
SETUP_KINDS = ("tasks", "done_tasks", "habits", "routines", "reminders")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolExpect(_Strict):
    name: str
    # one set of key args, or a list of alternatives (e.g. habit_id or habit_name)
    args: dict[str, Any] | list[dict[str, Any]] = Field(default_factory=dict)

    def alternatives(self) -> list[dict[str, Any]]:
        return self.args if isinstance(self.args, list) else [self.args]

    @model_validator(mode="after")
    def _known_tool_and_args(self) -> "ToolExpect":
        tool = TOOLS.get(self.name)
        if tool is None:
            raise ValueError(f"unknown tool {self.name!r}")
        for args in self.alternatives():
            unknown = set(args) - set(tool.input_model.model_fields)
            if unknown:
                raise ValueError(f"{self.name} has no argument(s) {sorted(unknown)}")
        return self


class TurnExpect(_Strict):
    intent: Intent | list[Intent] | None = None  # None: not scored (e.g. a bare "yes")
    tools: list[ToolExpect] = Field(default_factory=list)
    confirmation: bool = False  # the turn should end by asking the user to confirm

    def intent_ok(self, actual: str | None) -> bool:
        if self.intent is None:
            return True
        wanted = [self.intent] if isinstance(self.intent, str) else self.intent
        return actual in wanted


class Turn(_Strict):
    say: str
    expect: TurnExpect


class Setup(_Strict):
    prefs: dict[str, Any] = Field(default_factory=dict)
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    done_tasks: list[dict[str, Any]] = Field(default_factory=list)
    habits: list[dict[str, Any]] = Field(default_factory=list)
    routines: list[dict[str, Any]] = Field(default_factory=list)
    reminders: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)  # embedded with the real model


class EvalCase(_Strict):
    id: str
    now: AwareDatetime
    setup: Setup = Field(default_factory=Setup)
    turns: list[Turn] = Field(min_length=1)

    @model_validator(mode="after")
    def _refs_point_at_setup(self) -> "EvalCase":
        for ref in _refs(self):
            kind, _, index = ref.partition(".")
            if kind not in SETUP_KINDS or not index.isdigit():
                raise ValueError(f"{self.id}: bad ref {ref!r} (use e.g. tasks.0)")
            if int(index) >= len(getattr(self.setup, kind)):
                raise ValueError(f"{self.id}: ref {ref!r} has no matching setup item")
        return self


def _refs(case: EvalCase) -> list[str]:
    found = []
    for turn in case.turns:
        for tool in turn.expect.tools:
            for args in tool.alternatives():
                for value in args.values():
                    if isinstance(value, dict) and "ref" in value:
                        found.append(str(value["ref"]))
    return found


@dataclass
class SetupItems:
    """A case's `setup`, validated into the same DTOs the tools use."""

    prefs: dict[str, dict[str, Any]]
    tasks: list[TaskCreate]
    done_tasks: list[tuple[TaskCreate, datetime]]  # with completed_at
    habits: list[HabitCreate]
    routines: list[RoutineCreate]
    reminders: list[ReminderCreate]
    memories: list[tuple[str, MemoryKind]]


def build_setup(case: EvalCase) -> SetupItems:
    setup = case.setup
    # routines anchor on active_from; start them well before the case's day so they are due
    anchor = (case.now - timedelta(days=30)).date().isoformat()
    done = []
    for raw in setup.done_tasks:
        fields = {k: v for k, v in raw.items() if k != "completed_at"}
        done.append(
            (TaskCreate.model_validate(fields), datetime.fromisoformat(raw["completed_at"]))
        )
    return SetupItems(
        prefs={key: validate_preference(key, value) for key, value in setup.prefs.items()},
        tasks=[TaskCreate.model_validate(t) for t in setup.tasks],
        done_tasks=done,
        habits=[HabitCreate.model_validate(h) for h in setup.habits],
        routines=[
            RoutineCreate.model_validate({"active_from": anchor} | r) for r in setup.routines
        ],
        reminders=[ReminderCreate.model_validate(r) for r in setup.reminders],
        memories=[(m["content"], m.get("kind", "fact")) for m in setup.memories],
    )


def load_cases(path: Path = CASES_FILE) -> list[EvalCase]:
    cases = [EvalCase.model_validate(raw) for raw in yaml.safe_load(path.read_text("utf-8"))]
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate case ids: {dupes}")
    return cases


# --- scoring -------------------------------------------------------------------------------


@dataclass
class Observed:
    """What actually happened in one turn."""

    intent: str | None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    awaiting_confirmation: bool = False
    reply: str = ""


@dataclass
class TurnScore:
    intent_ok: bool
    problems: list[str]  # tool and confirmation problems; empty means the tools passed

    @property
    def tools_ok(self) -> bool:
        return not self.problems


def _as_instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _as_time(value: Any) -> time | None:
    if not isinstance(value, str) or ":" not in value or "T" in value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None


def same(expected: Any, actual: Any) -> bool:
    for convert in (_as_instant, _as_time):
        e, a = convert(expected), convert(actual)
        if e is not None and a is not None:
            return e == a
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() == actual.strip().lower()
    return bool(expected == actual)


def arg_matches(spec: Any, actual: Any, refs: dict[str, int]) -> bool:
    if isinstance(spec, dict) and len(spec) == 1:
        ((op, value),) = spec.items()
        if op == "contains":
            return isinstance(actual, str) and str(value).lower() in actual.lower()
        if op == "any_of":
            return any(arg_matches(v, actual, refs) for v in value)
        if op == "present":
            return (actual is not None) == bool(value)
        if op == "ref":
            return bool(actual == refs.get(str(value)))
    return same(spec, actual)


def call_matches(expect: ToolExpect, name: str, args: dict[str, Any], refs: dict[str, int]) -> bool:
    return name == expect.name and any(
        all(arg_matches(spec, args.get(key), refs) for key, spec in option.items())
        for option in expect.alternatives()
    )


def score_turn(expect: TurnExpect, seen: Observed, refs: dict[str, int]) -> TurnScore:
    """Expected tools must appear in order with matching key args; extra reads are fine, an
    unexpected write or destructive call is not."""
    problems: list[str] = []
    matched: set[int] = set()
    position = 0
    for wanted in expect.tools:
        hit = next(
            (i for i in range(position, len(seen.calls))
             if call_matches(wanted, *seen.calls[i], refs)),
            None,
        )  # fmt: skip
        if hit is None:
            got = [f"{n}({a})" for n, a in seen.calls] or ["no tool calls"]
            problems.append(f"expected {wanted.name}({wanted.args}); got {'; '.join(got)}")
        else:
            matched.add(hit)
            position = hit + 1
    for i, (name, _args) in enumerate(seen.calls):
        tool = TOOLS.get(name)
        if i not in matched and (tool is None or tool.tier is not ToolTier.READ):
            problems.append(f"unexpected call to {name}")
    if expect.confirmation != seen.awaiting_confirmation:
        problems.append(
            "expected a confirmation question" if expect.confirmation else "asked to confirm"
        )
    return TurnScore(intent_ok=expect.intent_ok(seen.intent), problems=problems)
