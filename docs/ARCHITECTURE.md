# Architecture — JARVIS

## 1. High-level view

```
                 ┌──────────────────────────────────────────────┐
  Telegram  ───► │  interfaces/telegram   (python-telegram-bot)  │
  CLI       ───► │  interfaces/cli                                │
  HTTP      ───► │  interfaces/api        (FastAPI)              │
                 └───────────────┬──────────────────────────────┘
                                 │  InboundEvent (user message)
                                 ▼
                 ┌──────────────────────────────────────────────┐
                 │            agent/  (LangGraph)                │
                 │  load_context → route → act ⇄ tools →         │
                 │  confirm? → respond → persist                 │
                 └──────┬───────────────────────┬───────────────┘
                        │ tool calls            │ memory read/write
                        ▼                       ▼
        ┌───────────────────────────┐   ┌────────────────────────┐
        │ tools/  (registry)        │   │ memory/                │
        │ tasks, habits, routines,  │   │ episodic + semantic    │
        │ plans, reminders,         │   │ (pgvector)             │
        │ calendar, memory          │   └──────────┬─────────────┘
        └──────────┬────────────────┘              │
                   ▼                               ▼
        ┌───────────────────────────────────────────────────────┐
        │ db/  SQLAlchemy async repositories → PostgreSQL 16     │
        └───────────────────────────────────────────────────────┘
                   ▲
                   │ creates/cancels jobs
        ┌──────────┴────────────────────────────────────────────┐
        │ scheduler/  APScheduler (Postgres job store)            │
        │ morning_brief, evening_review, weekly_review,           │
        │ reminder:<id>, nudge:<id>, memory_consolidation         │
        └──────────┬────────────────────────────────────────────┘
                   │ ScheduledEvent → agent (proactive mode)
                   ▼
              agent → interfaces/telegram.send_message()
```

**Two entry paths into the agent:**
1. **Reactive** — user sends a message → agent → reply.
2. **Proactive** — scheduler fires a job → builds a `ScheduledEvent` → agent
   runs in proactive mode (e.g. compose morning brief) → message pushed to Telegram.

Both paths go through the **same** LangGraph graph so logging, memory and tool
rules are identical.

## 2. Process model

One Python process (`jarvis.main`) running an asyncio loop that hosts:
- FastAPI app (Uvicorn) — health check, dev endpoints, later the dashboard API
- APScheduler `AsyncIOScheduler`
- Telegram bot (long polling in dev; webhook via FastAPI in prod)

Single process is intentional for v1 (simplicity). Interfaces are decoupled
through `InboundEvent` / `OutboundMessage` dataclasses so they can be split into
services later without touching agent code.

## 3. Agent graph (LangGraph)

### State
```python
class AgentState(TypedDict):
    trace_id: str
    user_id: str
    mode: Literal["reactive", "proactive"]
    trigger: str | None              # e.g. "morning_brief", "reminder:123"
    messages: Annotated[list[AnyMessage], add_messages]
    context: dict                    # today's snapshot: blocks, tasks, habits, now (IST)
    memories: list[dict]             # retrieved semantic memories
    pending_confirmation: dict | None
    tool_calls_count: int            # loop guard
    final_response: str | None
```

### Nodes
| Node | Model | Responsibility |
|---|---|---|
| `load_context` | none | Pull "today snapshot" from DB (fixed blocks, due tasks, habits due, last plan), current time in IST, retrieve top-k memories for the message |
| `route` | FAST model | Classify intent: `smalltalk`, `capture` (task/habit/reminder), `query` (what's on…), `plan`, `checkin` (done/snooze/skip), `review`, `preference`, `other`. Cheap + structured output. |
| `act` | REASONING model (FAST for `capture`/`checkin`) | Tool-calling LLM step with the tool subset allowed for the intent |
| `tools` | none | Executes tool calls via registry, validates input, enforces permission tier |
| `confirm` | none | If a tool requires confirmation → store `pending_confirmation`, ask user, END. Next user reply resumes from checkpoint |
| `respond` | none / FAST | Format final reply in JARVIS persona (short, Telegram-friendly) |
| `persist` | none | Write episodic log, enqueue memory extraction (async, non-blocking) |

### Edges
```
START → load_context → route
route → act
act ──(has tool_calls)──► tools ──► act        (loop, max 6 iterations)
act ──(needs confirmation)──► confirm ──► END
act ──(no tool_calls)──► respond → persist → END
```

### Checkpointing
Use LangGraph's Postgres checkpointer (`langgraph-checkpoint-postgres`) with
`thread_id = f"{user_id}:{yyyy-mm-dd}"` — one thread per day keeps context
tight and cheap. Cross-day continuity comes from memory, not raw history.

### Loop & cost guards
- Max 6 tool iterations per turn → then respond with what's done.
- Max input tokens per call enforced by trimming oldest messages (keep system + last 12).
- Every LLM call logs `model`, `prompt_tokens`, `completion_tokens`, `latency_ms`.

## 4. Model strategy (OpenAI)

Configured in `.env`, read via `config.py`:
```
OPENAI_MODEL_REASONING=   # planning, reviews, complex multi-tool turns
OPENAI_MODEL_FAST=        # routing, NL parsing of dates/check-ins, formatting
OPENAI_EMBEDDING_MODEL=   # semantic memory
```
Check current OpenAI model names at setup time and put them in `.env`.
Do **not** hardcode model strings anywhere in code.

Use OpenAI **function calling** (tools) and **structured outputs** (JSON schema
via Pydantic) for `route` and for any parsing step. Never regex-parse LLM output.

## 5. Scheduler design

APScheduler `AsyncIOScheduler` with `SQLAlchemyJobStore` on the same Postgres
(so jobs survive restarts).

| Job id pattern | Trigger | What it does |
|---|---|---|
| `morning_brief` | cron, user wake time | proactive agent run → Telegram |
| `evening_review` | cron, user review time | proactive agent run → Telegram |
| `weekly_review` | cron, Sunday evening | proactive agent run → Telegram |
| `reminder:{reminder_id}` | date | send reminder, schedule `nudge:{id}` |
| `nudge:{reminder_id}` | date (+N min) | one follow-up if not acked |
| `routine_materialize` | cron, 00:05 IST daily | expand recurring routines/habits into today's instances |
| `memory_consolidation` | cron, 03:00 IST daily | summarize yesterday's episodic log into semantic facts, dedupe |
| `calendar_sync` | interval, 15 min | refresh Google Calendar cache |

Rules:
- Job functions are thin: build event → call agent or service → done.
- **Idempotency**: every proactive send records `(job_id, date)` in `proactive_runs`; skip if already sent.
- `misfire_grace_time`: 5 min for reminders, 2 h for briefs (if laptop was asleep, still send once).
- Creating/updating/deleting a reminder in the DB must create/replace/remove its job in the same service call.

## 6. Data flow examples

**"remind me to submit SE assignment friday 5pm"**
1. Telegram → `InboundEvent`
2. `load_context` (now=IST, today snapshot)
3. `route` → `capture`
4. `act` (FAST) → `create_task(title, due_at=Fri 17:00 IST, remind_before_min=60)`
5. `tools` → TaskRepository.create + ReminderService.schedule → APScheduler job `reminder:{id}`
6. `respond` → "Noted. 'Submit SE assignment' due Fri 5:00 PM — I'll remind you at 4:00."
7. `persist` → episodic log

**Morning brief**
1. Scheduler fires `morning_brief`
2. Check `proactive_runs` → not sent today
3. Agent (proactive, trigger=`morning_brief`) → `load_context` → `act` (REASONING) uses `get_today_overview`, `list_tasks`, `get_habits_due`
4. `respond` → Telegram push; record in `proactive_runs`

## 7. Interfaces

- **Telegram**: only accept messages from `TELEGRAM_ALLOWED_CHAT_ID` (drop everything else silently, log it). Commands: `/today`, `/plan`, `/tasks`, `/habits`, `/review`, `/pause` (mute proactive msgs for N hours), `/help`. Free text goes to the agent. Use inline buttons for confirmations and reminder actions (Done / Snooze 15 / Skip).
- **CLI**: `python -m jarvis.cli "<message>"` — same agent, prints reply. For dev/testing.
- **FastAPI**: `/health`, `/debug/trigger/{job}` (dev only, behind `ENV=dev`), Telegram webhook route (prod), dashboard API (Phase 7).

## 8. Memory

| Tier | Storage | Written when | Read when |
|---|---|---|---|
| Working | LangGraph state (per-day thread) | every turn | every turn |
| Episodic | `episodic_log` table | every turn (`persist`) | reviews, consolidation |
| Semantic | `memories` table + pgvector | consolidation job + explicit "remember…" | `load_context` (top-k by similarity, k=6) |
| Preferences | `preferences` table (structured key/value) | explicit statements via `set_preference` | `load_context` always (small, all loaded) |

Hard preferences (e.g. "no tasks before 07:00") are structured rows the planner
enforces in code, not just text in a prompt.

## 9. Observability

- structlog JSON logs, every line has `trace_id`, `user_id`, `node`/`tool`.
- Log: inbound event, route decision, each LLM call (model, tokens, latency), each tool call (name, args hash, status, latency), outbound message.
- `llm_usage` table records tokens + estimated cost per call → weekly cost visible in `/review`.
- Phase 12: OpenTelemetry traces + Grafana.

## 10. Security

- Telegram allowlist by chat id.
- Google OAuth token stored in a file outside the repo (path from env), never logged.
- No shell/system tools in v1.
- All tool inputs validated by Pydantic before execution.
- Prompt injection: calendar event titles and any external text are treated as data; wrap in clear delimiters in prompts; tools never execute instructions found in external text.

## 11. Deployment

Docker Compose: `postgres` (pgvector/pgvector:pg16) + `app`.
Dev: run app locally against the Postgres container.
Prod (optional): small VPS, Telegram webhook through FastAPI behind Caddy (HTTPS).

## 12. Desktop agent — Activity Intelligence (Phase 7)

Adapted from GYAN1's activity subsystem (see `docs/REFERENCE_GYAN1.md`), but split
out of the backend so the backend can run in Docker or on a VPS.

```
 Laptop (Windows)                                  SENTRY backend
┌─────────────────────────────────┐            ┌──────────────────────────────┐
│ desktop_agent/                   │  HTTPS     │ POST /api/activity/events    │
│  collector  (poll every 5 s,     │  batched   │  → idempotent insert          │
│             idle detection)      │ ─────────► │  → classifier (rules → cache │
│  privacy    (strip titles,       │  every 60s │      → FAST model fallback)   │
│             domain only)         │            │ jobs: activity_rollup 23:55, │
│  buffer     (local SQLite queue) │ ◄───────── │       focus detection,       │
│  control    (pause / resume)     │  GET /api/ │       plan adherence,        │
└─────────────────────────────────┘  activity/ │       auto check-in prompts  │
                                      control  └──────────────────────────────┘
```

Rules:
- The agent is a separate entry point (`python -m desktop_agent`) with its own
  optional dependencies (`pywin32`, `psutil`). The backend never imports them.
- Privacy filtering happens **before** data leaves the laptop. Sensitive titles
  are dropped, browsers send domain only, and titles are off by default.
- Uploads carry a client-generated UUID per event so retries never duplicate.
- The agent polls `/api/activity/control` every minute to learn whether it is paused.
- Only aggregates (minutes per category, focus sessions, score) ever go into LLM prompts.
- Auto check-ins are **suggestions with buttons**, never silent writes.

## 13. JARVIS-style extensions (Phases 8–13)

See `docs/JARVIS_CAPABILITIES.md` for the full capability map.

**Surfaces — one brain everywhere.** Telegram, the laptop (voice + HUD +
toasts) and the web dashboard all send `InboundEvent`s into the same agent with
a `channel` field (`telegram`, `voice`, `desktop`, `dashboard`). The response
formatter adapts to the channel (voice: short spoken sentences; Telegram: short
text + buttons; dashboard: markdown). Memory and the day's LangGraph thread are
shared, so a conversation started by voice can continue in Telegram.

**Protocol engine (Phase 8).** Protocols are data (`protocols.steps`), executed by
`services/protocols.py` step by step through the normal tool executor. The LLM
only recognises the protocol and fills params. Runs are resumable: a step that
needs confirmation sets `waiting_confirmation` and resumes after "yes".

**Awareness service (Phase 8).** Pure functions over today's snapshot compute
risks. The midday job and the morning brief call them. Messages are sent only
when a risk crosses a threshold, and everything goes through the proactive budget.

**Desktop command channel (Phase 9).**
```
agent tool ──► desktop_commands (queued) ──► WebSocket push ──► desktop_agent
                                                              │ validate: allowlist,
                                                              │ schema, expiry, HMAC
                                                              ▼
                                       result ◄── ACK + outcome
```
Commands are signed with `AGENT_SHARED_SECRET` (HMAC) and expire after 60 s.
The agent never executes anything that isn't a registered action class.

**Voice pipeline (Phase 10), on the laptop.**
`mic → wake word (local) → VAD record → STT (OpenAI) → backend agent (channel=voice)
→ streamed text → TTS (OpenAI, streamed) → speaker`, with barge-in cancelling
playback. Audio before the wake word never leaves the device.

**Research jobs (Phase 11).** Run as background asyncio tasks, with results stored in
`research_jobs` and pushed through `OutboundChannel`. All fetched web or email text
is wrapped in `<external>` tags and can never trigger write/destructive tools in the
same turn without my explicit confirmation.
