---
description: Verify the current phase's acceptance criteria before moving on
---

1. Read `CURRENT_PHASE` from `CLAUDE.md`.
2. In `docs/ROADMAP.md`, go through every **Acceptance** item for that phase.
3. For each one, actually verify it (run the command, test, or script) — don't
   assume from the checkbox. Report: ✅ passed / ❌ failed (with the reason).
4. Also run: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy sentry_ai`.
5. If everything passes, say so and tell me to bump `CURRENT_PHASE`.
   Do NOT bump it yourself.
6. If anything fails, list the smallest set of fixes needed and ask whether to apply them.
