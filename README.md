# SENTRY

A JARVIS-style, single-user, always-on personal AI that runs my daily routine: plans the
day, reminds me proactively, tracks habits and tasks, and reviews the day with me.

Status: **Phase 0 (foundation)**. The API boots and `/health` checks the database. AI
features arrive phase by phase; see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (running)
- [uv](https://docs.astral.sh/uv/) (it installs the pinned Python 3.13 if you don't have it)
- git

## Setup

```bash
git clone https://github.com/jackstealer/SENTRY.git
cd SENTRY
cp .env.example .env              # PowerShell: Copy-Item .env.example .env
docker compose up -d postgres     # Postgres 16 + pgvector on 127.0.0.1:5432
uv sync                           # creates .venv from uv.lock
uv run alembic upgrade head       # enables the vector + pg_trgm extensions
uv run pre-commit install         # ruff + mypy run on every commit
```

Fill keys in `.env` only when a phase needs them. Phase 0 runs without any. The OpenAI
key and model names are optional now and enable the LLM smoke test.

## Run

```bash
uv run python -m sentry_ai.main
```

Then open <http://localhost:8000/health>. You should see `{"status":"ok","db":"ok"}`, or
HTTP 503 with `"db":"error"` if Postgres is down. Logs are JSON lines with a `trace_id`.

## Test and lint

```bash
uv run pytest -q                  # needs Postgres running; uses a separate sentry_test DB
uv run ruff check . && uv run ruff format --check .
uv run mypy sentry_ai
```

Tests never touch your dev data: they create and migrate `sentry_test` automatically. The
one real OpenAI call (`tests/test_smoke.py`) is skipped unless `OPENAI_API_KEY` and
`OPENAI_MODEL_FAST` are set.

## Troubleshooting

- **Port 8000 already in use**: set `API_PORT=8001` (or any free port) in `.env`.
- **Slow DB connections on Windows**: keep `127.0.0.1` in `DATABASE_URL`. With
  `localhost`, Windows tries IPv6 first and every connection waits about 2 s.
- **`ZoneInfoNotFoundError`**: run `uv sync`. Windows has no timezone database, so it
  comes from the `tzdata` package.

## Where things are

| Path | What |
|---|---|
| `CLAUDE.md` | Rules and entry point for Claude Code |
| `docs/` | The spec: architecture, data model, tools, persona, roadmap, conventions |
| `PHASE_PROMPTS.md` | The plan/build prompts used for each phase |
| `sentry_ai/` | Backend package |
| `migrations/` | Alembic migrations |
| `tests/` | pytest suite (mirrors `sentry_ai/`) |
