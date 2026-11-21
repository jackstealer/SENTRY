"""Deterministic day planner: the LLM decides what matters today, this decides where it fits.

Pure Python: no LLM, no I/O (services/planner_inputs.py loads a day from the DB).
Guarantees (property-tested): placed blocks never overlap each other or a fixed block plus
BUFFER, never fall in quiet hours, and stay between max(wake_time, no_tasks_before) and
sleep_time. Fixed blocks (fixed routines, calendar events) are inputs: clashes between them
are reported, never resolved.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sentry_ai.services.intervals import Interval, daily, intersect, subtract
from sentry_ai.services.time import combine_local, day_bounds_utc, parse_hhmm, to_local, to_utc

BUFFER = timedelta(minutes=10)  # kept free around every placed block
DEFAULT_TASK_MIN = 30  # tasks without an estimate
HABIT_BLOCK_MIN = 30  # habits have no duration column
FLEX_SHIFT = timedelta(hours=2)  # how far a flexible routine or habit may move
DEEP_WORK_MIN = 45  # tasks at least this long prefer the deep_work_window
DEFAULT_DAY = (time(7), time(23))  # when wake_time/no_tasks_before/sleep_time are unset
DEFAULT_REMINDER_MIN = 10
_SLOT_SECONDS = 15 * 60  # a plan for today starts at the next quarter hour
_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Prefs:
    """The hard and soft preferences the planner enforces (DATA_MODEL section 7)."""

    wake: time | None = None
    no_tasks_before: time | None = None
    sleep: time | None = None
    quiet: tuple[time, time] | None = None
    deep_work: tuple[time, time] | None = None
    max_tasks: int | None = None
    reminder_min: int = DEFAULT_REMINDER_MIN

    @classmethod
    def from_preferences(cls, prefs: Mapping[str, Mapping[str, Any]]) -> "Prefs":
        """From stored canonical values, e.g. {"wake_time": {"time": "06:30"}}."""

        def at(key: str) -> time | None:
            return parse_hhmm(prefs[key]["time"]) if key in prefs else None

        def window(key: str) -> tuple[time, time] | None:
            if key not in prefs:
                return None
            return parse_hhmm(prefs[key]["start"]), parse_hhmm(prefs[key]["end"])

        return cls(
            wake=at("wake_time"),
            no_tasks_before=at("no_tasks_before"),
            sleep=at("sleep_time"),
            quiet=window("quiet_hours"),
            deep_work=window("deep_work_window"),
            max_tasks=prefs.get("max_tasks_per_day", {}).get("count"),
            reminder_min=prefs.get("default_reminder_min", {}).get("minutes", DEFAULT_REMINDER_MIN),
        )


BusyKind = Literal["routine", "calendar_event"]
PlacedKind = Literal["routine", "habit", "task"]


@dataclass(frozen=True)
class Busy:
    """Fixed in time: a fixed routine or a calendar event."""

    start: datetime
    end: datetime
    title: str
    kind: BusyKind
    ref_id: int | None


@dataclass(frozen=True)
class Flexible:
    """Placed as near its usual time as possible: a non-fixed routine or a timed habit."""

    kind: Literal["routine", "habit"]
    ref_id: int
    title: str
    preferred: datetime
    minutes: int


@dataclass(frozen=True)
class TaskItem:
    id: int
    title: str
    priority: int
    due_at: datetime | None
    minutes: int
    project: str | None = None


@dataclass(frozen=True)
class Placed:
    start: datetime
    end: datetime
    kind: PlacedKind
    ref_id: int
    title: str


@dataclass(frozen=True)
class Unplaced:
    kind: PlacedKind
    ref_id: int
    title: str
    minutes: int
    reason: str


@dataclass(frozen=True)
class PlanDraft:
    local_date: date
    placed: tuple[Placed, ...]
    unplaced: tuple[Unplaced, ...]  # did not fit: this is the overcommitment
    deferred: tuple[Unplaced, ...]  # over max_tasks_per_day, or its time has passed
    conflicts: tuple[str, ...]
    free: tuple[Interval, ...]  # what is still free after placing

    @property
    def overcommitted_by_min(self) -> int:
        return sum(item.minutes for item in self.unplaced)

    @property
    def free_minutes(self) -> int:
        return sum(span.minutes for span in self.free)


def _next_slot(now: datetime) -> datetime:
    return datetime.fromtimestamp(math.ceil(now.timestamp() / _SLOT_SECONDS) * _SLOT_SECONDS, UTC)


def frame(
    day: date,
    prefs: Prefs,
    *,
    now: datetime | None = None,
    windows: Sequence[Interval] | None = None,
) -> list[Interval]:
    """Where anything flexible may go: day start to sleep_time, minus quiet hours, from now on."""
    starts = [t for t in (prefs.wake, prefs.no_tasks_before) if t is not None]
    first = max(starts) if starts else DEFAULT_DAY[0]
    last = prefs.sleep or DEFAULT_DAY[1]
    start = to_utc(combine_local(day, first))
    end = to_utc(combine_local(day if last > first else day + _ONE_DAY, last))
    if now is not None:
        start = max(start, _next_slot(now))
    spans = [Interval(start, end)] if start < end else []
    if prefs.quiet is not None:
        for quiet in daily(day, prefs.quiet):
            spans = subtract(spans, quiet)
    return spans if windows is None else intersect(spans, windows)


def free_time(
    day: date,
    prefs: Prefs,
    busy: Iterable[Busy],
    *,
    now: datetime | None = None,
    windows: Sequence[Interval] | None = None,
) -> list[Interval]:
    """The frame minus every fixed block padded by BUFFER."""
    spans = frame(day, prefs, now=now, windows=windows)
    for block in busy:
        spans = subtract(spans, Interval(block.start - BUFFER, block.end + BUFFER))
    return sorted(spans)


def order_tasks(
    tasks: Iterable[TaskItem],
    *,
    urgent_before: datetime,
    task_order: Sequence[int] = (),
    focus: str | None = None,
) -> list[TaskItem]:
    """LLM's explicit order first, then focus matches, then overdue/due-today, priority, due."""
    rank = {task_id: i for i, task_id in enumerate(task_order)}
    topic = focus.strip().lower() if focus else ""

    def key(task: TaskItem) -> tuple[int, bool, bool, int, datetime, int]:
        about = f"{task.title} {task.project or ''}".lower()
        urgent = task.due_at is not None and task.due_at < urgent_before
        return (
            rank.get(task.id, len(rank)),
            not (topic and topic in about),
            not urgent,
            task.priority,
            task.due_at or datetime.max.replace(tzinfo=UTC),
            task.id,
        )

    return sorted(tasks, key=key)


def _earliest(
    space: Iterable[Interval], minutes: int, deadline: datetime | None
) -> Interval | None:
    need = timedelta(minutes=minutes)
    for span in sorted(space):
        end = span.start + need
        if end <= span.end and (deadline is None or end <= deadline):
            return Interval(span.start, end)
    return None


def _nearest(free: Iterable[Interval], preferred: datetime, minutes: int) -> Interval | None:
    need = timedelta(minutes=minutes)
    best: tuple[tuple[timedelta, datetime], Interval] | None = None
    for span in free:
        if span.end - span.start < need:
            continue
        start = min(max(preferred, span.start), span.end - need)
        key = (abs(start - preferred), start)
        if key[0] <= FLEX_SHIFT and (best is None or key < best[0]):
            best = (key, Interval(start, start + need))
    return None if best is None else best[1]


def _fit(free: list[Interval], deep: list[Interval], task: TaskItem) -> Interval | None:
    attempts: list[tuple[list[Interval], datetime | None]] = []
    if deep and task.minutes >= DEEP_WORK_MIN:
        attempts.append((intersect(free, deep), task.due_at))
    attempts.append((free, task.due_at))
    if task.due_at is not None:
        attempts.append((free, None))  # late beats not at all
    for space, deadline in attempts:
        slot = _earliest(space, task.minutes, deadline)
        if slot is not None:
            return slot
    return None


def hhmm(at: datetime) -> str:
    return to_local(at).strftime("%H:%M")


def conflicts(busy: Iterable[Busy]) -> list[str]:
    items = sorted(busy, key=lambda b: (b.start, b.end, b.title))
    found: list[str] = []
    for i, first in enumerate(items):
        for other in items[i + 1 :]:
            if other.start >= first.end:
                break
            found.append(
                f"'{first.title}' ({hhmm(first.start)}-{hhmm(first.end)}) overlaps "
                f"'{other.title}' ({hhmm(other.start)}-{hhmm(other.end)})"
            )
    return found


def plan_day(
    day: date,
    prefs: Prefs,
    *,
    busy: Sequence[Busy] = (),
    flexible: Sequence[Flexible] = (),
    tasks: Sequence[TaskItem] = (),
    now: datetime | None = None,
    windows: Sequence[Interval] | None = None,
    task_order: Sequence[int] = (),
    focus: str | None = None,
) -> PlanDraft:
    free = free_time(day, prefs, busy, now=now, windows=windows)
    placed: list[Placed] = []
    unplaced: list[Unplaced] = []

    def take(slot: Interval, kind: PlacedKind, ref_id: int, title: str) -> None:
        nonlocal free
        placed.append(Placed(slot.start, slot.end, kind, ref_id, title))
        free = subtract(free, Interval(slot.start - BUFFER, slot.end + BUFFER))

    passed: list[Unplaced] = []
    for item in sorted(flexible, key=lambda f: (f.preferred, f.kind, f.ref_id)):
        if now is not None and item.preferred + FLEX_SHIFT < now:
            reason = "its usual time has passed"  # not overcommitment: the day moved on
            passed.append(Unplaced(item.kind, item.ref_id, item.title, item.minutes, reason))
            continue
        slot = _nearest(free, item.preferred, item.minutes)
        if slot is None:
            reason = f"no free {item.minutes} min within 2 h of {hhmm(item.preferred)}"
            unplaced.append(Unplaced(item.kind, item.ref_id, item.title, item.minutes, reason))
        else:
            take(slot, item.kind, item.ref_id, item.title)

    ordered = order_tasks(
        tasks, urgent_before=day_bounds_utc(day)[1], task_order=task_order, focus=focus
    )
    cap = len(ordered) if prefs.max_tasks is None else prefs.max_tasks
    deep = daily(day, prefs.deep_work) if prefs.deep_work else []
    for task in ordered[:cap]:
        slot = _fit(free, deep, task)
        if slot is None:
            reason = f"no free {task.minutes}-min slot"
            unplaced.append(Unplaced("task", task.id, task.title, task.minutes, reason))
        else:
            take(slot, "task", task.id, task.title)
    deferred = tuple(passed) + tuple(
        Unplaced("task", t.id, t.title, t.minutes, "over max_tasks_per_day") for t in ordered[cap:]
    )
    return PlanDraft(
        local_date=day,
        placed=tuple(sorted(placed, key=lambda p: (p.start, p.kind, p.ref_id))),
        unplaced=tuple(unplaced),
        deferred=deferred,
        conflicts=tuple(conflicts(busy)),
        free=tuple(sorted(free)),
    )


def check_slot(
    day: date, prefs: Prefs, slot: Interval, others: Iterable[tuple[str, Interval]]
) -> str | None:
    """Why a hand-picked slot breaks the planner's rules (move_block), or None if it is fine."""
    if not any(span.contains(slot) for span in frame(day, prefs)):
        return "it is outside your planning hours (day start to sleep time, minus quiet hours)"
    for title, other in others:
        if slot.overlaps(other):
            return f"it would overlap '{title}' ({hhmm(other.start)}-{hhmm(other.end)})"
    return None
