"""One repository per aggregate (DATA_MODEL section 9). Repos take (session, user_id), return
DTOs, and never commit: the caller owns the transaction."""

from sentry_ai.db.repositories.calendar import CalendarRepo
from sentry_ai.db.repositories.habits import HabitRepo
from sentry_ai.db.repositories.memory import MemoryRepo
from sentry_ai.db.repositories.plans import PlanRepo
from sentry_ai.db.repositories.preferences import PreferenceRepo
from sentry_ai.db.repositories.reminders import ReminderRepo
from sentry_ai.db.repositories.routines import RoutineRepo
from sentry_ai.db.repositories.system import SystemRepo
from sentry_ai.db.repositories.tasks import TaskRepo

__all__ = [
    "CalendarRepo",
    "HabitRepo",
    "MemoryRepo",
    "PlanRepo",
    "PreferenceRepo",
    "ReminderRepo",
    "RoutineRepo",
    "SystemRepo",
    "TaskRepo",
]
