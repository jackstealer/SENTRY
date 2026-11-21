# Data Model — SENTRY

PostgreSQL 16 + pgvector. All tables managed with **Alembic** migrations.
All timestamps are `TIMESTAMPTZ` stored in UTC. Local dates (a "day" in the
user's life) are stored as `DATE` computed in `USER_TZ` (Asia/Kolkata).

Conventions:
- PK: `id BIGSERIAL` unless stated.
- Every user-owned table has `user_id TEXT NOT NULL` (single value in v1, from config).
  Child tables (`plan_blocks`) inherit ownership through their parent; system tables
  (`proactive_runs`, `llm_usage`) are process-wide and have no `user_id`.
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at` where rows are mutated.
- Soft-delete with `archived_at TIMESTAMPTZ NULL` for user-facing entities (tasks, habits, routines). Hard delete only via explicit destructive tool.
- Enums as `TEXT` + `CHECK` constraint (easier migrations than PG enums).

---

## 1. Routines (recurring fixed blocks)

Things that repeat on a schedule: classes, gym, sleep window, meals.

```sql
routines (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,                       -- "College classes", "Gym"
  category TEXT NOT NULL,                    -- study | fitness | health | personal | work | other
  rrule TEXT NOT NULL,                       -- RFC 5545 RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
  start_time TIME NOT NULL,                  -- local (USER_TZ)
  duration_min INT NOT NULL CHECK (duration_min > 0),
  is_fixed BOOLEAN NOT NULL DEFAULT true,    -- fixed = planner must not move it
  remind_before_min INT NULL,                -- NULL = no reminder
  active_from DATE NULL,
  active_until DATE NULL,
  archived_at TIMESTAMPTZ NULL,
  created_at, updated_at
)
```

## 2. Tasks (one-off to-dos)

```sql
tasks (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  title TEXT NOT NULL,
  notes TEXT NULL,
  status TEXT NOT NULL DEFAULT 'todo'
    CHECK (status IN ('todo','in_progress','done','skipped','cancelled')),
  priority SMALLINT NOT NULL DEFAULT 2 CHECK (priority BETWEEN 1 AND 4), -- 1 = highest
  due_at TIMESTAMPTZ NULL,
  estimate_min INT NULL,
  scheduled_start TIMESTAMPTZ NULL,           -- set when placed in a day plan
  project TEXT NULL,                          -- free-text tag: "DSA", "SE assignment"
  source TEXT NOT NULL DEFAULT 'chat',        -- chat | review_rollover | calendar | api
  completed_at TIMESTAMPTZ NULL,
  rolled_over_count INT NOT NULL DEFAULT 0,   -- how many times pushed to next day
  archived_at TIMESTAMPTZ NULL,
  created_at, updated_at
)
INDEX (user_id, status, due_at)
```

## 3. Habits

```sql
habits (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,                         -- "Workout", "DSA practice"
  rrule TEXT NOT NULL,                        -- when it's due
  target_per_period INT NOT NULL DEFAULT 1,   -- e.g. 1 per day, 4 per week
  period TEXT NOT NULL DEFAULT 'day' CHECK (period IN ('day','week')),
  preferred_time TIME NULL,
  remind_before_min INT NULL,
  archived_at TIMESTAMPTZ NULL,
  created_at, updated_at
)

habit_logs (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  habit_id BIGINT NOT NULL REFERENCES habits(id),
  local_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('done','skipped','missed')),
  note TEXT NULL,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (habit_id, local_date)
)
```
Streaks are **computed** from `habit_logs` (no stored streak column — avoids drift).
Rules (`sentry_ai/services/streaks.py`):
- Day habits: consecutive **due** days (per the RRULE) marked `done`. Non-due days never break it.
- `skipped` is neutral (neither extends nor breaks). `missed`, or a past due day with no log, breaks.
  Nothing writes `missed` rows automatically: absence on a past due day already counts as missed.
- Today not yet logged does not break the streak.
- Week habits: consecutive Monday–Sunday weeks with `done` ≥ `target_per_period` (any day of the
  week counts). The current week counts once met and is ignored until then. A past week whose
  `skipped` days cover the shortfall is excused (neutral).
- Nothing before the habit's first day (creation day, or an earlier backfilled log) counts.

## 4. Day plans

```sql
day_plans (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  local_date DATE NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed','superseded')),
  summary TEXT NULL,                          -- LLM one-liner
  created_at, updated_at
)
-- only one confirmed plan per day:
CREATE UNIQUE INDEX uq_day_plans_confirmed
  ON day_plans (user_id, local_date) WHERE status = 'confirmed';

plan_blocks (
  id BIGSERIAL PK,
  plan_id BIGINT NOT NULL REFERENCES day_plans(id) ON DELETE CASCADE,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL CHECK (end_at > start_at),
  kind TEXT NOT NULL CHECK (kind IN ('routine','task','habit','calendar_event','break','buffer')),
  ref_id BIGINT NULL,                         -- id in routines/tasks/habits (by kind)
  title TEXT NOT NULL,
  gcal_event_id TEXT NULL,                    -- set after writing to SENTRY calendar
  outcome TEXT NULL CHECK (outcome IN ('done','partial','skipped','moved'))
)
```

## 5. Reminders

```sql
reminders (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  target_kind TEXT NOT NULL CHECK (target_kind IN ('task','habit','routine','plan_block','freeform')),
  target_id BIGINT NULL,
  message TEXT NOT NULL,
  fire_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled','sent','acked','snoozed','skipped','cancelled','expired')),
  nudge_sent BOOLEAN NOT NULL DEFAULT false,
  job_id TEXT NULL,                            -- APScheduler job id
  telegram_message_id BIGINT NULL,
  created_at, updated_at
)
INDEX (user_id, status, fire_at)
```

## 6. Calendar cache

```sql
calendar_events (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  gcal_event_id TEXT NOT NULL,
  calendar_id TEXT NOT NULL,
  title TEXT NOT NULL,
  start_at TIMESTAMPTZ NOT NULL,
  end_at TIMESTAMPTZ NOT NULL,
  all_day BOOLEAN NOT NULL DEFAULT false,
  is_sentry_owned BOOLEAN NOT NULL DEFAULT false,
  synced_at TIMESTAMPTZ NOT NULL,
  UNIQUE (user_id, calendar_id, gcal_event_id)
)
```

## 7. Memory

```sql
CREATE EXTENSION IF NOT EXISTS vector;

episodic_log (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','assistant','tool','system_event')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',       -- intent, tool name, trigger, etc.
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
INDEX (user_id, created_at)

memories (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  content TEXT NOT NULL,                      -- "Prefers deep work after 9pm"
  kind TEXT NOT NULL CHECK (kind IN ('fact','preference','pattern','goal')),
  embedding vector(1536) NOT NULL,            -- match OPENAI_EMBEDDING_MODEL dims
  confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT NOT NULL CHECK (source IN ('explicit','consolidation')),
  last_used_at TIMESTAMPTZ NULL,
  superseded_by BIGINT NULL REFERENCES memories(id),
  created_at, updated_at
)
INDEX USING hnsw (embedding vector_cosine_ops)

preferences (
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,                          -- e.g. "no_tasks_before", "deep_work_window"
  value JSONB NOT NULL,                       -- e.g. {"time":"07:00"}
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
)
```
If the embedding model changes dimensions, write a migration + re-embed script.

### Known preference keys (planner enforces these in code)
| key | value shape | meaning |
|---|---|---|
| `wake_time` | `{"time":"06:30"}` | morning brief time |
| `sleep_time` | `{"time":"23:30"}` | nothing scheduled after |
| `evening_review_time` | `{"time":"22:00"}` | |
| `weekly_review` | `{"day":"SU","time":"20:00"}` | |
| `no_tasks_before` | `{"time":"07:00"}` | hard constraint |
| `deep_work_window` | `{"start":"21:00","end":"23:00"}` | soft preference |
| `default_reminder_min` | `{"minutes":15}` | |
| `quiet_hours` | `{"start":"23:30","end":"06:30"}` | no proactive messages |
| `max_tasks_per_day` | `{"count":5}` | |

## 8. System tables

```sql
proactive_runs (                              -- idempotency for scheduled sends
  job_key TEXT NOT NULL,                      -- "morning_brief"
  local_date DATE NOT NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (job_key, local_date)
)

llm_usage (
  id BIGSERIAL PK,
  trace_id TEXT NOT NULL,
  model TEXT NOT NULL,
  purpose TEXT NOT NULL,                      -- route | act | respond | embed | consolidate
  prompt_tokens INT, completion_tokens INT,
  latency_ms INT,
  est_cost_usd NUMERIC(10,6),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)

-- APScheduler creates its own `apscheduler_jobs` table.
-- LangGraph checkpointer creates its own checkpoint tables.
```

## 9. Repository layer

One repository class per aggregate in `sentry_ai/db/repositories/`:
`RoutineRepo`, `TaskRepo`, `HabitRepo`, `PlanRepo`, `ReminderRepo`,
`CalendarRepo`, `MemoryRepo`, `PreferenceRepo`, `SystemRepo`.

- Repos take an `AsyncSession`, return Pydantic DTOs (not ORM objects) to callers.
- No business logic in repos. Business logic lives in `sentry_ai/services/`
  (e.g. `ReminderService` keeps DB row and APScheduler job in sync).

## 10. Activity Intelligence (Phase 7)

Data comes from the **desktop agent** (already privacy-filtered on the laptop).
Only aggregates are ever sent to the LLM — never raw event rows.

```sql
activity_events (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  client_event_id UUID NOT NULL,              -- generated by the agent → idempotent uploads
  device TEXT NOT NULL,                       -- "laptop"
  application TEXT NOT NULL,                  -- "code.exe", "chrome.exe"
  domain TEXT NULL,                           -- browser only, domain only ("leetcode.com")
  window_title TEXT NULL,                     -- NULL unless ACTIVITY_STORE_TITLES=true and not sensitive
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL CHECK (ended_at >= started_at),
  is_idle BOOLEAN NOT NULL DEFAULT false,
  category TEXT NULL,                         -- filled by classifier: study | coding | communication | entertainment | social | other
  productivity TEXT NULL CHECK (productivity IN ('productive','neutral','distracting')),
  local_date DATE NOT NULL,                   -- computed in USER_TZ
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, client_event_id)
)
INDEX (user_id, local_date, started_at)

app_classifications (                         -- cache: classify each app/domain once
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,                          -- "app:code.exe" or "domain:youtube.com"
  category TEXT NOT NULL,
  productivity TEXT NOT NULL CHECK (productivity IN ('productive','neutral','distracting')),
  source TEXT NOT NULL CHECK (source IN ('rule','llm','user')),  -- user overrides win
  confidence REAL NOT NULL DEFAULT 1.0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
)

focus_sessions (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  local_date DATE NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL,
  duration_min INT NOT NULL,
  main_category TEXT NOT NULL,
  top_apps JSONB NOT NULL DEFAULT '[]',       -- [{"key":"app:code.exe","min":38}, ...]
  interruptions INT NOT NULL DEFAULT 0,
  matched_plan_block_id BIGINT NULL REFERENCES plan_blocks(id),
  matched_habit_id BIGINT NULL REFERENCES habits(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
INDEX (user_id, local_date)

activity_daily (                              -- one row per day, written by activity_rollup
  user_id TEXT NOT NULL,
  local_date DATE NOT NULL,
  active_min INT NOT NULL,
  productive_min INT NOT NULL,
  neutral_min INT NOT NULL,
  distracting_min INT NOT NULL,
  idle_min INT NOT NULL,
  focus_min INT NOT NULL,
  productivity_score SMALLINT NOT NULL CHECK (productivity_score BETWEEN 0 AND 100),
  score_explanation TEXT NOT NULL,
  by_category JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (user_id, local_date)
)
```

New preference keys:
| key | value shape | meaning |
|---|---|---|
| `focus_min_minutes` | `{"minutes":20}` | min length of a focus session |
| `focus_max_gap_minutes` | `{"minutes":3}` | interruption tolerance |
| `distraction_nudges` | `{"enabled":false,"max_per_day":2}` | optional nudges in focus blocks |
| `activity_retention_days` | `{"days":90}` | raw events older than this are deleted by a nightly job (daily rollups kept) |

## 11. Protocols, timers, desktop commands, research (Phases 8–11)

```sql
protocols (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,                         -- "Focus Protocol"
  trigger_phrases TEXT[] NOT NULL DEFAULT '{}',
  description TEXT NOT NULL,
  steps JSONB NOT NULL,                       -- [{"tool":"start_sprint","args":{"minutes":"{{minutes|50}}"}}, ...]
  params_schema JSONB NOT NULL DEFAULT '{}',  -- JSON schema for run-time params
  is_builtin BOOLEAN NOT NULL DEFAULT false,
  schedule_cron TEXT NULL,                    -- optional auto-run (Morning Protocol)
  archived_at TIMESTAMPTZ NULL,
  created_at, updated_at,
  UNIQUE (user_id, name)
)

protocol_runs (
  id BIGSERIAL PK,
  protocol_id BIGINT NOT NULL REFERENCES protocols(id),
  trace_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','waiting_confirmation','completed','failed','cancelled')),
  current_step INT NOT NULL DEFAULT 0,
  params JSONB NOT NULL DEFAULT '{}',
  step_results JSONB NOT NULL DEFAULT '[]',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ NULL
)

timers (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('sprint','countdown')),
  title TEXT NOT NULL,
  starts_at TIMESTAMPTZ NOT NULL,
  ends_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','completed','stopped')),
  linked_task_id BIGINT NULL REFERENCES tasks(id),
  protocol_run_id BIGINT NULL REFERENCES protocol_runs(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)

desktop_commands (                            -- backend → desktop agent
  command_id UUID PRIMARY KEY,
  user_id TEXT NOT NULL,
  action TEXT NOT NULL,                       -- must be in the agent allowlist
  args JSONB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','delivered','succeeded','failed','rejected','expired')),
  result JSONB NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)

research_jobs (
  id BIGSERIAL PK,
  user_id TEXT NOT NULL,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
  report_md TEXT NULL,
  sources JSONB NOT NULL DEFAULT '[]',        -- [{"title":..., "url":...}]
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ NULL
)
```

Email is **not stored** — the Gmail digest is fetched live and only the
generated digest text goes to `episodic_log`.

New preference keys:
| key | value shape | meaning |
|---|---|---|
| `max_proactive_per_day` | `{"count":8}` | cap on unsolicited messages (reminders I set excluded) |
| `midday_check_time` | `{"time":"14:00"}` | situational-awareness check |
| `default_sprint_min` | `{"minutes":50}` | Focus Protocol default |
| `voice_reply` | `{"telegram":false,"desktop":true}` | speak replies or not |
| `reminder_surface` | `{"prefer":"desktop_when_active"}` | toast vs Telegram |
