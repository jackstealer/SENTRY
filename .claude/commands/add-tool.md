---
description: Add a new agent tool following TOOLS_SPEC conventions
argument-hint: <tool_name> <one-line purpose>
---

Add a new tool: $ARGUMENTS

1. Read `docs/TOOLS_SPEC.md` and an existing tool in `sentry_ai/tools/` as a pattern.
2. Propose: name (`verb_noun`), tier, Pydantic input model, return data, which
   intent subsets it joins. Wait for my OK if the tier is `destructive`.
3. Implement in the right `sentry_ai/tools/<group>.py`, register in `registry.py`.
4. Write tests: happy path, invalid input, timezone edge, DB side effects,
   and (if destructive) refusal without confirmation.
5. Add the tool to the catalogue table in `docs/TOOLS_SPEC.md`.
6. Run `pytest -q`, `ruff`, `mypy`; summarise.
