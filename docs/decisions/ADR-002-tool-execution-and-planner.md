# ADR-002: Tool execution model and deterministic planner

- Status: accepted
- Date: 2026-11-22

## Context
Phase 2 makes tools the only way the LLM changes anything (TOOLS_SPEC). Three choices
shape every later phase:
- who owns the DB transaction when one agent turn makes several tool calls;
- how destructive tools pause for the user;
- how a day plan gets its times without the LLM inventing overlapping timestamps.

## Decisions
1. **One savepoint per tool call; the caller commits once per turn.** The executor wraps
   each call in `session.begin_nested()`. It releases the savepoint on `ok=True` and rolls it
   back on `ok=False` or a crash. A failed tool never leaves half its writes behind, and
   earlier successful calls in the same turn are kept.
2. **Destructive tools have a `preview`.** Unconfirmed calls return
   `confirmation_required` with `{tool, args, summary, expires_at}`. Phase 3's `confirm`
   node stores that and re-runs the call with `confirmed=True`. A preview that finds
   nothing to act on returns that failure instead of asking.
3. **A `manage` intent** exposes edit/delete tools (`update_habit`, `update_routine`,
   `cancel_reminder`, `archive_tasks`, `delete_*`). Without it, those tools would be in no
   intent subset and could never be called.
4. **Reminders go through `ReminderService` and a `ReminderScheduler` protocol.** Phase 2
   uses a no-op scheduler (rows only). Phase 4 plugs in APScheduler and schedules every
   pending row at startup.
5. **The planner is pure Python** (`services/planner.py` with `services/intervals.py`;
   `services/planner_inputs.py` loads the DB). It works only with half-open UTC intervals,
   and every placed block is carved out of a single free list. That makes "no overlaps, no
   quiet hours, nothing before `no_tasks_before`" true by construction, and a hypothesis
   property test checks it. Constants: 10-min buffer, 30-min default task, 30-min habit
   block, ±2 h shift for flexible items, and the deep-work window for tasks of 45 min or
   more. Tasks are never split. Fixed blocks are never moved; clashes are reported.

## Alternatives considered
- Commit inside each tool. Rejected: a turn that fails halfway leaves partial state, and
  tests cannot roll back cleanly.
- Ask before every write. Rejected: too noisy. TOOLS_SPEC asks for confirmation on
  destructive tools only, and write replies must say exactly what changed.
- Spread the edit/delete tools across `capture`/`review`. Rejected: it muddies those
  intents' prompts. One more router label is cheaper.
- Let the LLM produce plan timestamps, or use a constraint solver. Rejected: the LLM
  hallucinates overlaps, and a solver is overkill for one person's day. Greedy placement
  is predictable and explainable ("didn't fit: no free 60-min slot").

## Consequences
- Phase 3 commits once per `tools` step, not once per turn (ADR-003): checkpoints are saved after
  every node, so per-step commits keep the DB and the conversation state in agreement.
- Greedy placement can miss a packing that a solver would find. Overcommitment is
  reported, never hidden.
- The planner ignores travel time and energy levels. Phase 8 patterns may tune the
  constants, which all live in one place.
