# Tools Spec — SENTRY

Tools are the **only** way the LLM affects the world. Each tool:
- lives in `sentry_ai/tools/<group>.py`,
- has a Pydantic v2 input model (becomes the OpenAI function JSON schema),
- returns a `ToolResult` (`ok: bool`, `data: dict`, `message: str` short human summary, `error: str | None`),
- declares a `tier`: `read` | `write` | `destructive`,
- is registered in `sentry_ai/tools/registry.py`,
- is async, logged, and unit-tested.

## 1. Base interface

```python
class ToolTier(StrEnum):
    READ = "read"            # no side effects
    WRITE = "write"          # creates/updates user data, reversible
    DESTRUCTIVE = "destructive"  # deletes data, bulk changes, writes to external calendar in bulk

class ToolResult(BaseModel):
    ok: bool
    data: dict = {}
    message: str = ""        # one line, used by the LLM to phrase the reply
    error: str | None = None

class Tool(Protocol):
    name: str
    description: str          # written for the LLM: when to use it, when NOT to
    tier: ToolTier
    input_model: type[BaseModel]
    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...

class ToolContext(BaseModel):
    user_id: str
    trace_id: str
    now: datetime            # tz-aware, USER_TZ
    session: AsyncSession
    confirmed: bool = False  # True only when re-run after user confirmation
```

## 2. Confirmation flow (destructive tools)

1. LLM calls a destructive tool.
2. Executor sees `tier == DESTRUCTIVE and not ctx.confirmed` → does **not** run it.
3. Returns to graph → `confirm` node stores `pending_confirmation = {tool, args, summary}` in state and sends:
   "This will delete 4 completed tasks from last month. Proceed? [Yes] [No]"
4. User taps Yes → graph resumes from checkpoint → executor runs with `confirmed=True`.
5. Any other reply → pending confirmation cleared, treated as a new message.
6. Pending confirmations expire after 10 minutes.

`write`-tier tools run immediately but the reply must state exactly what changed
("Moved 'DSA set' to 6:00–7:00 PM.").

## 3. Date/time parsing

Natural-language times ("friday 5pm", "in 2 hours", "tomorrow morning") are
resolved by the LLM into **ISO 8601 with offset** in tool args, using
`context.now` (IST) given in the system prompt. Tools additionally validate:
- reject naive datetimes,
- reject times in the past for new reminders (return `ok=False`, ask to clarify),
- vague words map to defaults: morning=09:00, afternoon=14:00, evening=18:00, night=21:00, "tonight"=21:00.

## 4. Tool catalogue (v1)

### Overview / query (read)
| Tool | Input | Returns |
|---|---|---|
| `get_today_overview` | `date?` (default today) | fixed blocks, calendar events, confirmed plan, tasks due, habits due, reminders pending |
| `list_tasks` | `status?`, `due_before?`, `project?`, `limit=20` | tasks |
| `get_habits_status` | `period: day\|week` | each habit + done/target + current streak |
| `get_free_slots` | `date`, `min_duration_min=30` | free intervals respecting preferences + fixed blocks |
| `get_stats` | `range: week\|month` | completion %, habit %, rollover counts, plan adherence |

### Tasks (write)
| Tool | Input | Notes |
|---|---|---|
| `create_task` | `title`, `due_at?`, `priority?`, `estimate_min?`, `project?`, `remind_before_min?` | creates reminder via ReminderService if due_at + remind set |
| `update_task` | `task_id`, any editable field | reschedules reminder if due_at changes |
| `complete_task` | `task_id` | sets done + completed_at, cancels pending reminders |
| `skip_task` | `task_id`, `reason?` | |
| `find_task` | `query` | fuzzy match by title (pg_trgm) → used when user says "done with the SE thing" |

### Habits (write)
| Tool | Input |
|---|---|
| `create_habit` | `name`, `rrule`, `target_per_period?`, `period?`, `preferred_time?`, `remind_before_min?` |
| `log_habit` | `habit_id` or `habit_name`, `status: done\|skipped`, `date?`, `note?` |
| `update_habit` | `habit_id`, fields |

### Routines (write)
| Tool | Input |
|---|---|
| `create_routine` | `title`, `category`, `rrule`, `start_time`, `duration_min`, `is_fixed?`, `remind_before_min?` |
| `update_routine` | `routine_id`, fields |
| `list_routines` (read) | — |

### Planning
| Tool | Tier | Input | Notes |
|---|---|---|---|
| `propose_day_plan` | write | `date`, `available_windows?`, `focus?` | Deterministic scheduler in `services/planner.py` places tasks/habits into free slots respecting hard preferences; LLM only chooses ordering/priorities. Saves as `draft`. |
| `confirm_day_plan` | write | `plan_id`, `sync_to_calendar=true` | Marks confirmed, supersedes older, creates reminders, writes blocks to SENTRY calendar |
| `move_block` | write | `block_id`, `new_start` | reschedules reminder + gcal event |

**Planner split:** the LLM decides *what matters today*; `planner.py` (pure
Python, unit-tested) decides *where it fits*. Never let the LLM produce raw
timestamps for a whole plan — it hallucinates overlaps.

### Reminders (write)
| Tool | Input |
|---|---|
| `create_reminder` | `message`, `fire_at`, `target_kind?`, `target_id?` |
| `snooze_reminder` | `reminder_id`, `minutes` |
| `ack_reminder` | `reminder_id`, `action: done\|skip` | (also completes linked task/habit) |
| `cancel_reminder` | `reminder_id` |

### Calendar
| Tool | Tier | Notes |
|---|---|---|
| `get_calendar_events` | read | from cache, `start`, `end` |
| `create_calendar_block` | write | SENTRY calendar only |
| `delete_calendar_block` | destructive | SENTRY calendar only; never touches other calendars |

### Memory & preferences
| Tool | Tier | Input |
|---|---|---|
| `remember` | write | `content`, `kind` — when user says "remember that…" |
| `recall` | read | `query`, `k=5` |
| `set_preference` | write | `key` (must be a known key from DATA_MODEL §7), `value` |
| `get_preferences` | read | — |
| `forget` | destructive | `memory_id` or `query` |

### Housekeeping (destructive)
| Tool | Input |
|---|---|
| `archive_tasks` | `filter` (e.g. done before date) |
| `delete_habit` | `habit_id` |
| `delete_routine` | `routine_id` |

## 5. Tool subsets by intent

`act` only receives tools relevant to the routed intent (smaller prompt, fewer wrong calls):

| Intent | Tools exposed |
|---|---|
| capture | create_task, create_habit, create_routine, create_reminder, find_task |
| checkin | find_task, complete_task, skip_task, log_habit, ack_reminder, snooze_reminder |
| query | get_today_overview, list_tasks, get_habits_status, get_calendar_events, get_free_slots, list_routines |
| plan | get_today_overview, get_free_slots, list_tasks, propose_day_plan, confirm_day_plan, move_block |
| review | get_stats, get_habits_status, list_tasks, update_task, recall |
| preference | set_preference, get_preferences, remember, recall, forget |
| smalltalk | recall |
| other | all read tools |

Proactive triggers map to: morning_brief → `query` set; evening_review → `review` set; weekly_review → `review` set.

## 6. Testing requirements

For every tool:
- happy path,
- invalid input rejected by Pydantic,
- timezone edge case (e.g. 23:50 IST = previous UTC day),
- side effects verified in DB (and scheduler job created/removed where relevant),
- destructive tools: not executed without `confirmed=True`.

## 7. Activity tools (Phase 7)

| Tool | Tier | Input | Returns |
|---|---|---|---|
| `get_activity_summary` | read | `date?`, `range: day\|week` | active/productive/distracting minutes, score + explanation, top apps/domains (aggregates only) |
| `get_focus_sessions` | read | `date?` | focus sessions with matched plan block / habit |
| `set_activity_rule` | write | `key` ("app:x" / "domain:y"), `category`, `productivity` | stores a `source='user'` classification (overrides rule/LLM) |
| `pause_activity_tracking` | write | `hours` | tells the desktop agent to stop collecting until a time |

Add `get_activity_summary` + `get_focus_sessions` to the `query` and `review`
intent subsets; `set_activity_rule` and `pause_activity_tracking` to `preference`.

## 8. Phase 8–13 tools

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `run_protocol` | write | 8 | `name`, `params?`; steps keep their own tiers (a destructive step still asks) |
| `list_protocols` | read | 8 | |
| `create_protocol` | write | 8 | natural language → proposed steps shown to me → saved only after I confirm |
| `cancel_protocol_run` | write | 8 | |
| `start_sprint` / `stop_sprint` | write | 8 | `minutes`, `task_id?` |
| `create_countdown` | write | 8 | `title`, `ends_at` |
| `get_risks` | read | 8 | deadline risk, overload, conflicts, breaking streaks |
| `suggest_next` | read | 8 | next-best action for the current free slot |
| `get_system_status` | read | 8 | DB, scheduler, OpenAI, Calendar, desktop agent, cost |
| `desktop_open` | write | 9 | `target` = app key or http(s) URL from allowlist |
| `desktop_workspace` | write | 9 | `name` from USER_PROFILE workspaces |
| `desktop_close_distractions` | destructive | 9 | closes only allowlisted distracting apps |
| `desktop_status` | read | 9 | battery, CPU/RAM, online |
| `desktop_screenshot_explain` | read | 9 | one-off; image sent to the vision model only for this call |
| `web_search` | read | 11 | results wrapped in `<external>` |
| `read_url` | read | 11 | length-limited, `<external>` wrapped |
| `start_research` | write | 11 | async job, reply delivered later |
| `get_mail_digest` | read | 11 | Gmail read-only |
| `draft_email_reply` | write | 11 | creates a Gmail **draft**; there is no send tool |
| `extract_from_image` | read | 11 | returns proposed routines/tasks; saving needs confirmation |
| `get_health_summary` | read | 13 | sleep, steps, workouts |
| `home_scene` | write | 13 | Home Assistant scene, only if configured |

New intents for routing: `protocol` (run/list/create protocols, sprints, countdowns),
`research` (web_search, read_url, start_research), `comms` (get_mail_digest,
draft_email_reply), `device` (desktop_* tools), `system` (get_system_status).
Add `get_risks` and `suggest_next` to the `query` and `plan` subsets.

**Never add:** a generic shell/command tool, a code-execution tool, a file-write
tool, a send-email/send-message-to-others tool, or any person-identification tool.
