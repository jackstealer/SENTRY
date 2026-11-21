# Project Context — JARVIS

## 1. Vision

Full JARVIS-style capability map: `docs/JARVIS_CAPABILITIES.md`.


A personal AI that manages my routine the way JARVIS manages Tony Stark's life:
it knows my schedule, reminds me before I forget, adapts the plan when things
slip, tracks whether I'm actually doing what I said I would, and talks to me
like a sharp, slightly dry butler — not a generic chatbot.

The core difference from a to-do app: **JARVIS is proactive.** It initiates
conversations (morning brief, reminders, nudges, evening review). I should be
able to forget the app exists and still have my day run on track.

## 2. User

- Exactly one user: me. No auth system, no multi-tenancy.
- `user_id` still exists in every table (single hardcoded value) so the schema
  doesn't break if this ever grows.
- Timezone: `Asia/Kolkata` (IST, UTC+05:30).
- Primary device: phone (Telegram). Secondary: laptop (CLI / dashboard).
- My actual routine, goals and preferences are in `docs/USER_PROFILE.md`.

## 3. Core use cases (v1)

### UC-1 Morning brief (proactive)
At my configured wake time, JARVIS sends a Telegram message:
- today's fixed blocks (classes, gym, etc.) from my routine + Google Calendar,
- top 3 tasks for today (by priority/deadline),
- habits due today,
- anything overdue or slipping from yesterday,
- one line of weather-free, useful commentary ("You have a 2-hour gap after lunch — good slot for the DSA set you skipped yesterday.").

### UC-2 Plan my day (on demand)
"plan my day" / "I have 4 free hours, what should I do?" → JARVIS builds a
time-blocked plan from fixed blocks + task backlog + habits + energy
preferences, and asks for confirmation before writing it to the calendar.

### UC-3 Reminders & nudges (proactive)
- Fires reminders N minutes before each block/task (N configurable per item).
- If I don't acknowledge a reminder, one follow-up nudge, then stops (no spam).
- I can reply "snooze 20", "done", "skip", "move to 6pm" in natural language.

### UC-4 Task capture (natural language)
"remind me to submit the SE assignment Friday 5pm" → creates a task with due
date + reminder, confirms back in one line.

### UC-5 Habit tracking
Daily/weekly habits (e.g. workout, DSA practice, reading). JARVIS logs
check-ins from replies ("done gym"), tracks streaks, and surfaces patterns
("You've missed DSA every Thursday for 3 weeks.").

### UC-6 Evening review (proactive)
At configured time: what got done, what didn't, quick reflection prompt
("Anything blocking you tomorrow?"), and rolls unfinished tasks forward with my
approval.

### UC-7 Weekly review (proactive)
Sunday evening: habit completion %, tasks done vs planned, time-block
adherence, one concrete suggestion for next week.

### UC-8 Memory
JARVIS remembers stated preferences and facts ("I'm more focused at night",
"never schedule anything before 7am") and applies them in future plans without
me repeating them.

### UC-10 Activity awareness (Phase 7)
A desktop agent tracks which app/site is in focus on my laptop (privacy-filtered).
SENTRY detects focus sessions, scores the day, compares planned vs actual time,
and suggests habit check-ins from real activity ("45 min on LeetCode — log DSA as done?").

### UC-9 Calendar sync
Reads Google Calendar for fixed events; writes plan blocks to a dedicated
"JARVIS" calendar (never edits my other calendars).

## 4. Scope

### In scope (v1)
- Telegram bot interface (text) + dev CLI
- LangGraph agent with tool-calling (OpenAI)
- Routines, tasks, habits, reminders, day plans, reviews
- APScheduler-driven proactive messages
- Google Calendar read + write (JARVIS calendar only)
- Episodic + semantic memory (Postgres + pgvector)
- Structured logging + basic metrics
- Docker Compose deployment (laptop or a small VPS)

### Out of scope for v1 (do NOT build unless ROADMAP says so)
- Activity tracking desktop agent — Phase 7
- Protocols, situational awareness, timers, self-diagnostics — Phase 8
- Desktop control (allowlisted actions) — Phase 9
- Voice (wake word, STT/TTS) — Phase 10
- Research, Gmail triage, vision — Phase 11
- HUD + web dashboard — Phase 12
- Health data, smart home (optional) — Phase 13
- Arbitrary shell/code execution, ever (desktop control is allowlisted actions only)
- Sending email or messages to other people on my behalf (drafts only)
- Face recognition or identifying people
- Multi-user, auth, payments
- Mobile native app
- Fine-tuning any model

## 5. Non-functional requirements

| Requirement | Target |
|---|---|
| Reply latency (Telegram, simple command) | < 3 s p50 |
| Reminder accuracy | fires within ±60 s of scheduled time |
| Availability | survives restart without losing scheduled jobs (Postgres job store) |
| Cost | < ₹1,000/month OpenAI spend at normal usage — use the fast model for routing/parsing |
| Privacy | all data in my own Postgres; no third-party storage besides OpenAI calls + Google Calendar |
| Reliability | every proactive job is idempotent (no duplicate morning briefs after a restart) |

## 6. Success criteria for v1

1. For 7 consecutive days, I get a correct morning brief and evening review without touching the code.
2. Reminders fire on time and respond correctly to "done / snooze / skip / move".
3. I can add a task in natural language from Telegram in one message.
4. Habit streaks are accurate and visible via "/habits".
5. A preference I state once is applied in plans a week later.
6. Restarting the container loses nothing (jobs, state, memory).
