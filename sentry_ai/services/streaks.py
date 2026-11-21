"""Habit streaks, computed from habit_logs on demand (never stored, so they cannot drift).

Rules (DATA_MODEL section 3):
- day habits count consecutive *due* days (per the RRULE) marked done
- `skipped` is neutral: it neither extends nor breaks a streak
- a past due day with no log, or logged `missed`, breaks it
- today not logged yet does not break it (the day is not over)
- week habits count consecutive Monday-Sunday weeks with done >= target; the current
  week counts once met and is ignored until then; a past week whose skips cover the
  shortfall is excused (neutral)
- nothing before the habit's first day (creation day, or an earlier backfilled log) counts
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from sentry_ai.db.enums import HabitLogStatus
from sentry_ai.db.schemas import HabitDTO, HabitLogDTO
from sentry_ai.services.time import local_date_of, occurrence_dates, week_start


class Streak(BaseModel):
    model_config = ConfigDict(frozen=True)

    current: int
    unit: Literal["day", "week"]


def compute_streak(habit: HabitDTO, logs: Sequence[HabitLogDTO], today: date) -> Streak:
    statuses = {log.local_date: log.status for log in logs if log.habit_id == habit.id}
    statuses = {day: status for day, status in statuses.items() if day <= today}
    first_day = min([local_date_of(habit.created_at), *statuses])
    if habit.period == "week":
        return Streak(
            current=_weekly(statuses, habit.target_per_period, first_day, today), unit="week"
        )
    return Streak(current=_daily(statuses, habit.rrule, first_day, today), unit="day")


def _daily(statuses: dict[date, HabitLogStatus], rrule: str, first_day: date, today: date) -> int:
    streak = 0
    for day in reversed(occurrence_dates(rrule, first_day, first_day, today)):
        status = statuses.get(day)
        if status == "done":
            streak += 1
        elif status == "skipped" or (status is None and day == today):
            continue
        else:  # missed, or a past due day nobody logged
            break
    return streak


def _weekly(statuses: dict[date, HabitLogStatus], target: int, first_day: date, today: date) -> int:
    done = Counter(week_start(day) for day, status in statuses.items() if status == "done")
    skipped = Counter(week_start(day) for day, status in statuses.items() if status == "skipped")
    current_week, first_week = week_start(today), week_start(first_day)
    streak, week = 0, current_week
    while week >= first_week:
        if done[week] >= target:
            streak += 1
        elif week != current_week and done[week] + skipped[week] < target:
            break  # a finished week that fell short without enough skips
        week -= timedelta(days=7)
    return streak
