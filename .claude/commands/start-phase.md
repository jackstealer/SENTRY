---
description: Start (or continue) the current roadmap phase
---

1. Read `CLAUDE.md` and note `CURRENT_PHASE`.
2. Read every file in `docs/` listed under "Required reading" (skim ones already read this session).
3. Open `docs/ROADMAP.md`, find the current phase, list the unchecked tasks.
4. Pick the first unchecked task. Before coding, reply with:
   - what you'll build (3–5 bullets),
   - files you'll create/modify,
   - any decision you need from me (only if hard to undo).
5. Build it, write tests, run `pytest -q`, `ruff check .`, `mypy jarvis`.
6. Tick the task in `docs/ROADMAP.md`, then give me a short summary:
   files changed, how to run/test, anything I must do manually.
7. Stop after one task unless I said "keep going". $ARGUMENTS
