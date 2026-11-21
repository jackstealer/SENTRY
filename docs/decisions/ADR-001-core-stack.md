# ADR-001: Core stack and architecture

- Status: accepted (decision 2 amended by ADR-004: any OpenAI-compatible API)
- Date: 2026-11-21

## Context
Single-user, always-on routine assistant. Needs proactive behaviour (scheduled
messages), reliable reminders across restarts, structured data (tasks, habits,
plans) plus semantic memory, and an LLM that can call tools. Built by one
developer, so operational simplicity matters as much as capability.

## Decisions

1. **LangGraph for orchestration.** Explicit state machine with conditional
   edges, built-in checkpointing (needed for the confirmation pause/resume),
   and fits an existing learning track. A hand-rolled loop would need its own
   persistence for paused confirmations.

2. **OpenAI as LLM provider**, model names in env vars only. Two tiers: a fast
   model for routing/parsing/formatting and a reasoning model for planning and
   reviews. Keeps cost low and allows swapping models without code changes.

3. **PostgreSQL + pgvector as the only datastore.** Relational data, vector
   memory, APScheduler job store and LangGraph checkpoints all in one DB — one
   thing to back up and run. No Redis/Qdrant in v1.

4. **APScheduler 3.x with SQLAlchemy job store** for proactivity. In-process,
   persistent, good enough for one user. Celery/Temporal would be overkill.

5. **Telegram as primary interface.** Push notifications on phone for free,
   inline buttons for quick actions, no app to build. Web dashboard later.

6. **Deterministic planner + LLM prioritisation.** LLMs are bad at producing
   non-overlapping timestamps; Python slot-filling is testable. The LLM chooses
   *what* and *why*, code decides *where*.

7. **Single process, decoupled by interfaces.** `InboundEvent`,
   `OutboundChannel`, `ReminderScheduler` abstractions allow splitting into
   services later without rewriting agent logic.

## Alternatives considered
- Custom asyncio orchestrator — more control, but re-implements checkpointing.
- Separate vector DB (Qdrant) — better at scale, unnecessary for one user.
- Celery + Redis for scheduling — more moving parts, no benefit at this scale.
- WhatsApp interface — Business API is paid and more restrictive than Telegram.

## Consequences
- One Postgres instance is a single point of failure → nightly backups (Phase 14).
- Single process means a crash stops everything → Docker `restart: unless-stopped` + health check.
- Model availability changes over time → re-check model names in `.env` periodically.
