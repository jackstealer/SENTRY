# ADR-003: Agent graph: confirmations, commits, model tiers and cost

- Status: accepted
- Date: 2026-11-22

## Context
Phase 3 puts a LangGraph agent in front of the Phase 2 tools. Four choices needed settling:
- how a destructive call waits for the user's "yes";
- when the database commits, given that the checkpointer saves state after every node;
- which model tier each intent uses;
- how cost is estimated without putting model names (or prices) in code.

## Decisions
1. **Confirmation lives in state, not in `interrupt()`.** The `confirm` node stores
   `pending_confirmation` (tool, args, summary, expires_at), asks, and ends the turn. On the
   next message, `route` also returns `confirmation: yes|no|none`. A timely "yes" sends the
   turn to `run_pending`, which re-runs the call with `confirmed=True`. Anything else clears
   it and is handled as a new message.
2. **Commit per step.** Every node that writes (`tools`, `run_pending`, `persist`) opens one
   transaction for that step and commits when the step ends. Usage rows are written in their
   own transaction.
3. **Model tiers.** `route` uses FAST with structured output at temperature 0. `act` uses FAST
   for capture, checkin and smalltalk, and REASONING for everything else. A model that rejects
   `temperature` is retried once without it.
4. **Cost from `OPENAI_PRICES`** (`model=in/out`, USD per 1M tokens) in `.env`, next to the
   model names. Unknown models get a null cost and a single warning.
5. **History is trimmed by whole turns.** The current turn is always kept; earlier turns are
   added while the total stays within 12 messages and the 6,000-token budget. Cutting only at
   user messages never separates a tool call from its result, which the API rejects.

## Alternatives considered
- `interrupt()` / `Command(resume=...)`: native, but "any other reply is a new message" would
  need two graph calls per message.
- One commit per turn: a crash after a checkpoint would leave state saying "created" while the
  DB rolled back.
- REASONING for smalltalk: costlier, with no benefit (its only tool is `recall`).
- A price table in code: it would hardcode model names (hard rule 1).
- langchain's `trim_messages(start_on="human")`: it can drop the current turn when that turn
  alone is longer than the message cap.

## Consequences
- A turn that fails halfway may have committed earlier tool steps. That matches what the user
  saw, since each tool reply states exactly what changed.
- A confirmation asked just before midnight IST is lost at midnight (new thread). It would
  expire within 10 minutes anyway.
- Costs are only as accurate as `OPENAI_PRICES`; cached-input discounts are ignored.
