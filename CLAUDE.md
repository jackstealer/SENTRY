# CLAUDE.md — JARVIS (Personal Routine AI)

> Claude Code reads this file automatically at the start of every session.
> It is the single entry point. Everything else lives in `docs/` — read the
> files listed under "Required reading" before writing any code.

---

## 1. What this project is

JARVIS is a **single-user, always-on personal AI system that runs my daily
routine** — plans my day, reminds me proactively, tracks habits and tasks,
reviews my day with me, and learns my patterns over time. Think Tony Stark's
JARVIS, scoped to routine management first.

It is **not** a chatbot. It is a long-running backend service with:
- an LLM reasoning core (LangGraph + OpenAI),
- a scheduler that makes it *proactive* (it messages me, not just replies),
- a Postgres data layer for routines/tasks/habits/memory,
- interfaces: Telegram bot (primary), CLI (dev), web dashboard (later), voice (later).

## 2. Required reading (in this order)

| File | Why you need it |
|---|---|
| `docs/PROJECT_CONTEXT.md` | Vision, user, use cases, scope in/out |
| `docs/ARCHITECTURE.md` | Components, data flow, LangGraph graph design |
| `docs/DATA_MODEL.md` | Every table, column, and index — the schema is locked |
| `docs/TOOLS_SPEC.md` | Every tool the agent can call, with schemas + permission tiers |
| `docs/PERSONA.md` | JARVIS system prompt + tone rules |
| `docs/ROADMAP.md` | Phases, tasks, acceptance criteria — **work only on the current phase** |
| `docs/CONVENTIONS.md` | Code style, testing, logging, git rules |
| `docs/USER_PROFILE.md` | My actual routine, preferences, timezone (filled by me) |
| `docs/JARVIS_CAPABILITIES.md` | Which JARVIS/Karen/EDITH behaviours we build, which we never build, built-in protocols |
| `docs/REFERENCE_GYAN1.md` | A reference repo: which of its patterns we adopt, which we must NOT copy |
| `docs/decisions/` | Architecture Decision Records — why things are the way they are |

If any doc contradicts another, **stop and ask me**. Do not guess.

## 3. Current phase

```
CURRENT_PHASE: 0
```

> I update this line when a phase is done. Only build what the current phase
> in `docs/ROADMAP.md` lists. Do not pre-build future phases "while you're at it".

## 4. Tech stack (locked — see docs/decisions/)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ (backend), TypeScript/React (dashboard, Phase 7) |
| Agent orchestration | LangGraph (`StateGraph`, checkpointing via Postgres) |
| LLM provider | **OpenAI API** — model names come from env vars, never hardcoded |
| API server | FastAPI + Uvicorn |
| Scheduler | APScheduler (AsyncIOScheduler) with Postgres job store |
| Database | PostgreSQL 16 + pgvector, accessed via SQLAlchemy 2.0 (async) + Alembic |
| Validation | Pydantic v2 |
| Primary interface | Telegram bot (`python-telegram-bot` v21+, async) |
| Calendar | Google Calendar API (OAuth, my account only) |
| Logging | structlog (JSON) |
| Tests | pytest + pytest-asyncio |
| Packaging | `uv` (preferred) or pip + `requirements.txt` |
| Infra | Docker Compose (postgres, app) |
| Timezone | `Asia/Kolkata` default — all datetimes stored in UTC, converted at the edges |

## 5. Commands

```bash
# setup
cp .env.example .env            # then fill keys
docker compose up -d postgres
uv sync                          # or: pip install -r requirements.txt
alembic upgrade head

# run
uv run python -m jarvis.main             # starts API + scheduler + telegram bot
uv run python -m jarvis.cli "plan my day" # one-shot CLI for dev

# quality
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy jarvis
```

If a command here is wrong or missing, fix this section in the same commit.

## 6. Repo layout (target)

```
jarvis/
├── CLAUDE.md
├── docs/                    # all context docs (do not delete)
├── jarvis/
│   ├── main.py              # boots FastAPI + scheduler + telegram
│   ├── cli.py               # dev entry point
│   ├── config.py            # pydantic-settings, reads .env
│   ├── agent/               # LangGraph graph, nodes, state, prompts
│   ├── tools/               # one file per tool group, all registered in registry.py
│   ├── services/            # business logic: planner, reminders, time, routines
│   ├── scheduler/           # APScheduler setup, job definitions
│   ├── interfaces/          # telegram/, api/ (FastAPI routers)
│   ├── db/                  # models.py, session.py, repositories/
│   ├── memory/              # episodic + semantic memory manager
│   └── observability/       # logging, tracing setup
├── desktop_agent/           # laptop agent: activity (P7), actions (P9), voice (P10), HUD client (P12)
├── migrations/              # alembic
├── scripts/                 # seed_from_profile.py, google_auth.py, run_evals.py
├── tests/
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

## 7. Hard rules (never break these)

1. **Never hardcode secrets or model names.** Everything from `jarvis/config.py` → `.env`.
2. **Never commit `.env`**, tokens, `credentials.json`, or `token.json`.
3. **All datetimes are timezone-aware.** Store UTC, convert to `USER_TZ` only for display/parsing.
4. **Every tool has a Pydantic input schema and a permission tier** (`read` / `write` / `destructive`). Destructive tools must go through the confirmation flow in `docs/TOOLS_SPEC.md`.
5. **The LLM never writes to the DB directly.** It calls tools; tools call repositories.
6. **No schema changes without an Alembic migration** and an update to `docs/DATA_MODEL.md`.
7. **Every LLM call and tool call is logged** (structured, with `trace_id`).
8. **Tests for every tool and every repository function** before marking a task done.
9. **Don't add dependencies silently.** If you need a new library, say which and why.
10. **Work phase by phase.** Finish acceptance criteria of the current phase before anything else.
11. **Reference repos are for ideas, not code.** Never copy files from GYAN1 or any other repo into this one.
12. **Desktop control is allowlisted, typed actions only.** Never add shell, code-execution, file-write, send-message-to-others or person-identification tools.

## 8. How to work with me

- Before starting a task: restate what you'll build in 3–5 bullets, then go.
- If a requirement is ambiguous and the choice is hard to undo (schema, external API, data deletion): **ask first**.
- If it's cheap to change: pick the sensible option, note it in your summary.
- After each task: summary of files changed, how to run/test it, anything I need to do (API keys, OAuth, etc.).
- When an architectural choice is made, add an ADR in `docs/decisions/` (template in `ADR-000-template.md`).
- Keep responses short and practical. No essays.

## 9. Definition of done (per task)

- [ ] Code follows `docs/CONVENTIONS.md`
- [ ] Tests written and passing (`pytest -q`)
- [ ] `ruff` + `mypy` clean
- [ ] Logs show the full trace for the feature
- [ ] Relevant doc in `docs/` updated if behaviour/schema changed
- [ ] Acceptance criterion in `docs/ROADMAP.md` ticked
