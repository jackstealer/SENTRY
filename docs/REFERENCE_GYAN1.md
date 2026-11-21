# Reference repo — GYAN1

Repo: https://github.com/Xxierrr/GYAN1
A basic JARVIS-style assistant: FastAPI + React 19/Vite, Google Gemini,
async PostgreSQL, JWT auth, Windows activity tracking, focus sessions,
AI daily reports, browser voice, glassmorphism UI with a 3D "neural core".

**How to use this reference (Claude Code):**
- You may `git clone --depth 1` it into `../reference/GYAN1` (outside our repo) and read it.
- Borrow **ideas and patterns only**. Do not copy files or large code blocks — its
  license says "Private — Personal use only", and its code doesn't meet our
  conventions (no tests, no tz handling, no type-strictness).
- When you adopt one of the patterns below, say which one in your task summary.

---

## 1. What we adopt (and where it lands in our roadmap)

| GYAN1 feature | What we take | Our version | Phase |
|---|---|---|---|
| **Activity collector** (`backend/activity/collector.py`) | Poll the foreground window every N s, merge consecutive samples into sessions, idle detection, platform abstraction (`AbstractPlatformCollector`) | Separate **desktop agent** process on my laptop that posts batched events to the SENTRY API (backend may run in Docker/VPS, where Win32 isn't available) | 7 |
| **Privacy filter** (`activity/privacy.py`) | Sensitive-keyword title stripping, domain-only extraction from browser titles, consent flag | Same rules, run **on the desktop agent before upload** — raw titles never leave the laptop unless allowed | 7 |
| **Classifier** (`activity/classifier.py`) | App/domain → category + productive/neutral/distracting, with confidence | Rule table in config (editable in dashboard) first; FAST-model fallback for unknown apps, result cached so each app is classified once | 7 |
| **Focus session detector** (`activity/sessions.py`) | Continuous productive time ≥ 20 min, interruptions ≤ 3 min don't break it, count interruptions | Pure function + `hypothesis` tests; thresholds in `preferences` | 7 |
| **Productivity score** (`activity/productivity.py`) | 0–100 score with an explanation string | Score + explanation feed the evening review and weekly review | 7 |
| **Daily report with deterministic fallback** (`reports/generator.py`) | Aggregate → score → LLM insights → if LLM fails, still produce a stats-only report; send only aggregates to the LLM, never raw telemetry | Applies to **all** proactive messages: morning brief / evening review must still send a plain template if OpenAI is down | 4 (fallback), 7 (activity data) |
| **Model fallback chain** (`brain/agent.py`) | Try primary model, then fallbacks on error | `OPENAI_MODEL_FALLBACKS` (comma list) in config; retry with backoff first, then next model; log every fallback | 3 |
| **Tool registry with permission levels** (`tools/registry.py`) | Category + permission per tool, `execute()` returns success/error dict | Already in our TOOLS_SPEC (read/write/destructive); add `category` field for dashboard grouping | 2 |
| **WebSocket streaming with tool events** (`routers/chat.py`) | Events: `chunk`, `tool_start`, `tool_result` | Dashboard chat streams over WebSocket with the same event types | 12 |
| **Browser-native voice** (`VoiceControls.tsx`) | Web Speech API STT + SpeechSynthesis TTS, waveform visualizer | Free, zero-backend voice in the dashboard as a fallback to the laptop voice loop | 12 |
| **UI direction** (`index.css`, `InteractiveSphere.tsx`) | Dark glass panels, animated core visual, Productivity / Timeline / Daily Report pages | Theme + page ideas for our dashboard; keep it readable and fast on mobile | 12 |
| **Activity / report pages** | Timeline, category breakdown, focus session cards, productivity dashboard | Reuse the page concepts: Timeline, Focus, Insights | 12 |

## 2. What we deliberately do NOT copy (and why)

| GYAN1 does | Problem | We do instead |
|---|---|---|
| `run_command`, `run_python`, `write_file` marked **SAFE** (no confirmation) | Any prompt-injected text (a web page, a window title) could run code on my machine | Never. Laptop control is allowlisted typed actions only (Phase 9) |
| Smart-home `control_device` marked SAFE | Physical-world side effects without confirmation | Optional Phase 13 via Home Assistant scenes, only inside protocols I defined |
| Every tool exposed on every LLM call (~30 tools) | Costlier prompts, more wrong tool picks | Intent routing → small tool subset per turn |
| `datetime.now(timezone.utc).strftime("%Y-%m-%d")` for "today" | In IST the "day" flips at 5:30 AM, so reports land on the wrong date | All "which day" logic in `USER_TZ` via the time service |
| Hardcoded model list in code | Breaks when model names change | Model names only from env |
| JWT register/login system | Unneeded complexity for a single user | Telegram chat-id allowlist + one static dashboard token |
| `pywin32` in main `requirements.txt` | Breaks Linux/Docker installs | Windows-only deps live in the desktop agent's own `pyproject` extra |
| Broad `except Exception` that swallows errors, `logging` f-strings | Hard to debug, no traces | `ToolResult(ok=False)` for expected errors, structlog with `trace_id`, raise on bugs |
| No tests, no migrations in use | Regressions go unnoticed | Tests + Alembic per CONVENTIONS.md |
| Purely reactive chat assistant | Doesn't run a routine | Our scheduler makes SENTRY proactive; activity data closes the loop on whether plans were actually followed |

## 3. Why activity tracking matters for a routine assistant

GYAN1 tracks *what I did*; our plan tracks *what I meant to do*. Combining them
is SENTRY's edge:
- **Auto check-ins:** a 45-min VS Code + LeetCode focus session at 9 PM can
  auto-propose "Log DSA practice as done?" instead of waiting for me to report it.
- **Plan adherence:** evening review compares planned blocks with actual
  focus sessions ("Planned 2 h deep work, got 1 h 10 m — YouTube took 35 min of it.").
- **Honest weekly review:** patterns come from real data, not memory.
- **Distraction nudges (optional):** if 20+ min of "distracting" time lands
  inside a planned focus block, one gentle Telegram nudge (respecting quiet hours
  and a max-per-day limit).
