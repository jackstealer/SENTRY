"""Half-open UTC time intervals and the set maths the planner is built on."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sentry_ai.services.time import combine_local, to_utc

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, order=True)
class Interval:
    """Half-open [start, end) in UTC."""

    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: "Interval") -> bool:
        return self.start <= other.start and other.end <= self.end


def subtract(spans: Iterable[Interval], cut: Interval) -> list[Interval]:
    out: list[Interval] = []
    for span in spans:
        if not span.overlaps(cut):
            out.append(span)
            continue
        if span.start < cut.start:
            out.append(Interval(span.start, cut.start))
        if cut.end < span.end:
            out.append(Interval(cut.end, span.end))
    return out


def merge(spans: Iterable[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for span in sorted(spans):
        if merged and span.start <= merged[-1].end:
            merged[-1] = Interval(merged[-1].start, max(merged[-1].end, span.end))
        else:
            merged.append(span)
    return merged


def intersect(spans: Iterable[Interval], windows: Sequence[Interval]) -> list[Interval]:
    return merge(
        Interval(max(s.start, w.start), min(s.end, w.end))
        for s in spans
        for w in windows
        if s.overlaps(w)
    )


def daily(day: date, window: tuple[time, time]) -> list[Interval]:
    """A local HH:MM window (it may wrap midnight) on the days around `day`, in UTC."""
    start, end = window
    return [
        Interval(
            to_utc(combine_local(d, start)),
            to_utc(combine_local(d if end > start else d + _ONE_DAY, end)),
        )
        for d in (day - _ONE_DAY, day, day + _ONE_DAY)
    ]
