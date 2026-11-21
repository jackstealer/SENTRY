"""Property tests: whatever the tasks and fixed blocks, the planner's guarantees hold."""

from collections import Counter
from datetime import date, datetime, time, timedelta
from itertools import combinations, pairwise
from typing import Any
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from sentry_ai.services.intervals import Interval, daily, merge, subtract
from sentry_ai.services.planner import (
    BUFFER,
    Busy,
    Flexible,
    PlanDraft,
    Prefs,
    TaskItem,
    frame,
    plan_day,
)
from sentry_ai.services.time import combine_local

IST = ZoneInfo("Asia/Kolkata")
CLOCK = st.integers(0, 24 * 60 - 1).map(lambda m: time(m // 60, m % 60))
WINDOW = st.tuples(CLOCK, CLOCK).filter(lambda w: w[0] != w[1])


@st.composite
def cases(draw: st.DrawFn) -> dict[str, Any]:
    day = draw(st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31)))
    midnight = datetime.combine(day, time(), IST)

    def at(minutes: int) -> datetime:
        return midnight + timedelta(minutes=minutes)

    prefs = Prefs(
        wake=draw(st.none() | CLOCK),
        no_tasks_before=draw(st.none() | CLOCK),
        sleep=draw(st.none() | CLOCK),
        quiet=draw(st.none() | WINDOW),
        deep_work=draw(st.none() | WINDOW),
        max_tasks=draw(st.none() | st.integers(1, 10)),
    )
    # fixed blocks may start the evening before or spill into the next day, and may clash
    fixed = draw(st.lists(st.tuples(st.integers(-360, 1800), st.integers(5, 300)), max_size=6))
    busy = [Busy(at(s), at(s + d), f"fixed {i}", "routine", i) for i, (s, d) in enumerate(fixed)]
    flex = draw(st.lists(st.tuples(st.integers(0, 1439), st.integers(10, 120)), max_size=4))
    flexible = [
        Flexible("habit" if i % 2 else "routine", i, f"flex {i}", at(p), m)
        for i, (p, m) in enumerate(flex)
    ]
    specs = draw(
        st.lists(
            st.tuples(st.integers(1, 4), st.integers(5, 240), st.none() | st.integers(-1440, 2880)),
            max_size=15,
        )
    )
    tasks = [
        TaskItem(i, f"task {i}", p, None if due is None else at(due), m)
        for i, (p, m, due) in enumerate(specs)
    ]
    windows = draw(
        st.none()
        | st.lists(
            st.tuples(st.integers(0, 1439), st.integers(15, 600)).map(
                lambda w: Interval(at(w[0]), at(w[0] + w[1]))
            ),
            max_size=3,
        )
    )
    ids = [t.id for t in tasks]
    return {
        "day": day,
        "prefs": prefs,
        "busy": busy,
        "flexible": flexible,
        "tasks": tasks,
        "now": draw(st.none() | st.integers(-1440, 1440).map(at)),
        "windows": windows,
        "task_order": draw(st.lists(st.sampled_from(ids), unique=True)) if ids else [],
        "focus": draw(st.none() | st.sampled_from(["task 1", "task", "nothing"])),
    }


def run(case: dict[str, Any]) -> PlanDraft:
    return plan_day(
        case["day"],
        case["prefs"],
        busy=case["busy"],
        flexible=case["flexible"],
        tasks=case["tasks"],
        now=case["now"],
        windows=case["windows"],
        task_order=case["task_order"],
        focus=case["focus"],
    )


@settings(max_examples=300, deadline=None)
@given(cases())
def test_planner_guarantees(case: dict[str, Any]) -> None:
    plan = run(case)
    day, prefs = case["day"], case["prefs"]
    slots = [Interval(p.start, p.end) for p in plan.placed]

    # no overlaps: between placed blocks, or with any fixed block plus its buffer
    for a, b in combinations(slots, 2):
        assert not a.overlaps(b)
    for slot in slots:
        for block in case["busy"]:
            assert not slot.overlaps(Interval(block.start - BUFFER, block.end + BUFFER))

    # nothing inside quiet hours
    if prefs.quiet is not None:
        for quiet in daily(day, prefs.quiet):
            assert not any(slot.overlaps(quiet) for slot in slots)

    # nothing before no_tasks_before (or wake time), after sleep time, before now, or
    # outside the windows the user offered
    allowed = frame(day, prefs, now=case["now"], windows=case["windows"])
    for slot in slots:
        assert any(span.contains(slot) for span in allowed)
        if prefs.no_tasks_before is not None:
            assert slot.start >= combine_local(day, prefs.no_tasks_before)
        if case["now"] is not None:
            assert slot.start >= case["now"]

    # every item is accounted for exactly once, at its full length
    minutes = {("task", t.id): t.minutes for t in case["tasks"]}
    minutes |= {(f.kind, f.ref_id): f.minutes for f in case["flexible"]}
    seen = Counter(
        [(p.kind, p.ref_id) for p in plan.placed]
        + [(u.kind, u.ref_id) for u in (*plan.unplaced, *plan.deferred)]
    )
    assert seen == Counter(list(minutes))  # each key exactly once
    for p in plan.placed:
        assert Interval(p.start, p.end).minutes == minutes[(p.kind, p.ref_id)]
    placed_tasks = sum(p.kind == "task" for p in plan.placed)
    if prefs.max_tasks is not None:
        assert placed_tasks <= prefs.max_tasks

    # overcommitment is exactly the work that did not fit
    assert plan.overcommitted_by_min == sum(u.minutes for u in plan.unplaced)
    assert all(u.minutes == minutes[(u.kind, u.ref_id)] for u in plan.unplaced)

    # what is left free is really free
    for span in plan.free:
        assert not any(span.overlaps(slot) for slot in slots)

    # same input, same plan
    assert run(case) == plan


@given(
    st.lists(st.tuples(st.integers(0, 1000), st.integers(1, 300)), max_size=8),
    st.tuples(st.integers(0, 1000), st.integers(1, 300)),
)
def test_interval_maths_keeps_spans_sorted_disjoint_and_adds_up(
    raw: list[tuple[int, int]], cut_raw: tuple[int, int]
) -> None:
    base = datetime(2026, 11, 23, tzinfo=IST)

    def iv(start: int, length: int) -> Interval:
        return Interval(base + timedelta(minutes=start), base + timedelta(minutes=start + length))

    spans = merge(iv(*r) for r in raw)
    assert all(a.end < b.start for a, b in pairwise(spans))
    cut = iv(*cut_raw)
    left = subtract(spans, cut)
    assert not any(span.overlaps(cut) for span in left)
    removed = sum(max(0, min(s.end, cut.end) .timestamp() - max(s.start, cut.start).timestamp())
                  for s in spans) // 60  # fmt: skip
    assert sum(s.minutes for s in left) == sum(s.minutes for s in spans) - removed
