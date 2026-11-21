# ADR-004: OpenAI-compatible providers (Groq) via OPENAI_BASE_URL

- Status: accepted
- Date: 2026-11-22

## Context
ADR-001 picked OpenAI as the LLM provider. For development and the Phase 3 evals, the user
wants to use Groq's free tier instead. Groq serves an OpenAI-compatible API, so the same SDK,
messages, tool calling and structured outputs work against it.

## Decision
Add `OPENAI_BASE_URL` (blank = OpenAI). The OpenAI SDK, the `OPENAI_*` setting names and the
rule that model names live only in `.env` all stay. On Groq, `OPENAI_API_KEY` holds the Groq
key. Clearing the base URL switches back to OpenAI.

Retries honour a rate-limited response's `Retry-After` header for waits up to 30 s. A longer
wait (e.g. a daily cap) skips straight to the next fallback model instead of retrying.

## Alternatives considered
- Stay on OpenAI only. Rejected: it's paid, and the user wants a free option for now.
- Groq's own SDK, or a multi-provider library (LiteLLM). Rejected: new dependencies for an API
  we can already reach with the OpenAI SDK.
- Rename settings to `LLM_*`. Rejected: churn for no behavioural gain. "OPENAI_*" now means
  "OpenAI-compatible".

## Consequences
- On Groq, strict JSON-schema output (used by `route`) works only on some models (at the time
  of writing, `openai/gpt-oss-20b` and `openai/gpt-oss-120b`). Pick FAST and REASONING
  accordingly.
- Groq may not offer embeddings. Without `OPENAI_EMBEDDING_MODEL`, memory degrades:
  `remember`/`recall`/`forget` reply "not configured" and `load_context` skips memories.
  Everything else works. A separate embedding endpoint can be added later if needed.
- Free tiers rate-limit (requests per minute and tokens per day). Use
  `scripts/run_evals.py --pause 5`.
- `OPENAI_PRICES` of `0/0` makes costs read $0. That says nothing about what OpenAI would cost.
- User data (messages, tasks, memories) goes to whichever provider the base URL names.
