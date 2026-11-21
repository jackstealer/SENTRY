# Conventions — SENTRY

## 1. Python style

- Python 3.11+, full type hints, `mypy --strict` on `sentry_ai/`.
- Formatter + linter: `ruff format`, `ruff check` (rules: E, F, I, B, UP, ASYNC, SIM, RUF).
- Async everywhere on I/O paths (DB, HTTP, OpenAI, Telegram). No blocking calls inside the event loop — wrap sync libraries (Google API client) with `asyncio.to_thread`.
- Pydantic v2 for every boundary: tool inputs, DTOs, config, LLM structured outputs.
- No business logic in interfaces (Telegram/API/CLI) or repositories — it belongs in `services/` or tools.
- Small modules. If a file passes ~300 lines, split it.
- Names: `snake_case` functions/modules, `PascalCase` classes, `UPPER_CASE` constants. Tool names are `verb_noun` (`create_task`).
- Docstrings on public functions: one line on *why/when*, not a restatement of the signature.

## 2. Time handling (the #1 bug source in this project)

- Never call `datetime.now()` / `datetime.utcnow()` directly. Use `services.time.now_utc()` / `now_local()` so tests can freeze time (`time-machine`).
- Never create naive datetimes. A test should fail if one sneaks in.
- DB stores UTC `TIMESTAMPTZ`; "which day is this" is always computed in `USER_TZ`.
- Every feature involving dates gets a test around midnight IST (18:30 UTC boundary).

## 3. LLM usage

- All OpenAI calls go through `agent/llm.py` — never instantiate the client elsewhere.
- Model names only from config.
- Structured output (Pydantic → JSON schema) for anything parsed by code.
- Prompts live in `agent/prompts/*.md` (Jinja2), not inline strings.
- Temperature: 0 for route/parse, ≤ 0.4 for responses.
- Never send secrets, tokens or file paths into prompts.

## 4. Logging

- `structlog`, JSON output, `trace_id` bound per inbound event/scheduled job.
- Event names are `snake_case` verbs: `tool_called`, `llm_call_completed`, `reminder_fired`.
- Log args as summaries for anything personal-heavy; never log tokens/keys.
- Levels: DEBUG (dev detail), INFO (business events), WARNING (recoverable), ERROR (needs attention).

## 5. Testing

- `pytest` + `pytest-asyncio` (auto mode) + `time-machine` + `hypothesis` (planner).
- Test DB: real Postgres (docker), each test in a transaction rolled back at the end. No SQLite substitutes — pgvector/pg_trgm must be real.
- LLM is **mocked** in unit tests (fake client returning canned tool calls). Real-API checks live in `tests/evals/` and run via `scripts/run_evals.py`, not default `pytest`.
- Layout mirrors source: `tests/tools/test_tasks.py` ↔ `sentry_ai/tools/tasks.py`.
- Coverage target: ≥ 80% for `services/` and `tools/`.

## 6. Database

- Every schema change = Alembic migration + `docs/DATA_MODEL.md` update in the same commit.
- Migrations must be reversible (`downgrade` implemented).
- Use repositories; no raw SQL in tools/services except via repository methods.

## 7. Errors

- Tools return `ToolResult(ok=False, error=...)` for expected failures (not found, invalid time). Raise only for bugs.
- External API calls: timeout + retry with exponential backoff (`tenacity`), max 3 attempts.
- User-facing errors are one sentence + trace id. Never show stack traces in Telegram.

## 8. Git

- Branch per task: `phase-<n>/<task-id>-<slug>` (e.g. `phase-2/2.5-task-tools`).
- Conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Commit when a task's tests pass. Don't mix tasks in one commit.
- Never commit `.env`, `*.token.json`, `credentials*.json`, `__pycache__`, `.venv`.

## 9. Dependencies

Approved for v1 (add others only after asking):
`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`,
`asyncpg`, `psycopg[binary]` (for LangGraph checkpointer), `alembic`, `pgvector`,
`langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`, `openai`,
`apscheduler<4`, `python-telegram-bot`, `python-dateutil`, `jinja2`, `structlog`,
`tenacity`, `google-api-python-client`, `google-auth-oauthlib`,
dev: `pytest`, `pytest-asyncio`, `time-machine`, `hypothesis`, `ruff`, `mypy`, `pre-commit`.

Desktop agent only (separate optional extra `[desktop]`): `psutil`, `pywin32` (Windows), `httpx`, `websockets` (P9), `openwakeword`, `sounddevice`, `webrtcvad` or `silero-vad` (P10).
Later backend additions (ask before adding, phase noted): search API client or plain `httpx` (P11), `google-api-python-client` Gmail scopes (P11), Tauri or Electron for the HUD (P12, decided in an ADR).

Note: pin APScheduler to 3.x — 4.x has a different API and is not used here.
