# JARVIS Capabilities → SENTRY Features

What the MCU assistants (JARVIS in Iron Man/Avengers, Karen in Spider-Man:
Homecoming, EDITH in Far From Home) actually *do* on screen, broken into
capability groups, and how each becomes a real, buildable SENTRY feature.

Rule for Claude Code: this doc explains **why** features exist. The **what /
when** lives in `docs/ROADMAP.md`. Build only what the current phase lists.

---

## Capability map

| # | Movie behaviour | Capability | SENTRY feature | Phase |
|---|---|---|---|---|
| 1 | Wakes Tony with the time, weather and schedule; "Good morning, sir" | **Briefings** | Morning brief, evening review, weekly review, on-demand `/status` | 4 ✔ |
| 2 | Talks back with dry wit, calls him "sir", pushes back on bad ideas | **Personality** | PERSONA.md: composed, witty, honest; challenges unrealistic plans | 3 ✔ |
| 3 | Reminds Tony of meetings and obligations he's ignoring | **Proactive reminders** | Reminders, nudges, snooze/skip | 4–5 ✔ |
| 4 | Manages Tony's calendar, schedules around him | **Scheduling** | Day planner + Google Calendar | 2, 6 ✔ |
| 5 | Remembers everything Tony told it, past context | **Memory** | Episodic + semantic memory, preferences | 6 ✔ |
| 6 | Tracks what Tony is doing in the lab | **Activity awareness** | Desktop agent, focus sessions, planned vs actual | 7 ✔ |
| 7 | "House Party Protocol", "Clean Slate Protocol" — one command triggers a whole sequence | **Protocols** | Named multi-step macros: `Focus Protocol`, `Night Protocol`, `Exam Protocol`, user-defined | 8 |
| 8 | "Power at 12%", "Sir, there's a threat incoming" — constant risk assessment | **Situational awareness** | Deadline-risk scoring, overload/conflict detection, midday check-in, slipping-streak alerts | 8 |
| 9 | Runs timers/countdowns, "Time to impact…" | **Timers & sprints** | Focus sprints (Pomodoro-style), countdowns to deadlines/exams | 8 |
| 10 | Self-reports: "All systems operational", flags its own failures | **Self-diagnostics** | `/systems` health report: DB, scheduler, OpenAI, Calendar, desktop agent, cost today | 8 |
| 11 | Controls the house and workshop: lights, screens, displays, opens files | **Device / computer control** | Desktop agent actions: open apps/URLs, launch "workspaces", focus mode (close distracting apps), laptop status, screenshots | 9 |
| 12 | Always listening; hands-free conversation while Tony works | **Voice-first** | Wake word on laptop, streaming STT/TTS, barge-in, conversation mode, Telegram voice notes | 10 |
| 13 | "Run a search on…", pulls up files and research, analyses data | **Research** | Web search tool, async deep-research jobs delivered as reports, summarise links/PDFs | 11 |
| 14 | Screens calls and messages, flags what matters | **Comms triage** | Gmail read-only digest of important mail + reply drafts (never sends without confirmation) | 11 |
| 15 | Scans objects and documents and explains them (EDITH/Karen visuals) | **Vision** | Photo of timetable → routines; photo of notice/assignment → tasks; screenshot → explanation. **No face recognition.** | 11 |
| 16 | Holographic HUD with live stats, Karen's suit display | **HUD** | Always-on-top desktop mini-HUD (next block, timer, streak, alerts) + full web dashboard | 12 |
| 17 | Monitors Tony's vitals inside the suit | **Health monitoring** | Sleep/steps/workout data from Google Fit / Health Connect, hydration & break reminders, sleep-debt-aware planning | 13 |
| 18 | Runs the whole house | **Smart home** | Optional Home Assistant integration (lights/scenes as protocol steps), only if I own devices | 13 (optional) |
| 19 | Works across house, suit, phone, glasses — same assistant everywhere | **Multi-surface continuity** | One brain, one memory, many surfaces: Telegram, laptop voice, HUD, dashboard; conversation continues across them | 5, 9, 10, 12 |
| 20 | Karen's "Would you like to…?" social tips; JARVIS suggests better options | **Recommendations** | Suggests next best action from plan + activity + energy ("You've got 40 free minutes — the graph problems you skipped fit.") | 8 |

---

## Deliberately excluded (movie features we don't build)

| Movie feature | Why not |
|---|---|
| Facial recognition / identifying people (EDITH, Karen) | Privacy and legal risk; not needed for a routine assistant |
| Surveillance of other people / network access (EDITH) | Unethical, illegal |
| Weapons, "instant kill mode", drones | Obviously not |
| Unrestricted computer control ("JARVIS, do anything") | Prompt-injection risk. Only allowlisted, typed actions; anything destructive asks first |
| Fully autonomous messaging on my behalf | SENTRY drafts; I send. No auto-sent emails or messages to other people |

---

## Built-in protocols (Phase 8 defaults; I can edit or add more)

Protocols are named sequences of **existing tool calls** stored as data, so
they run deterministically without the LLM re-planning each time. The LLM's job
is only to recognise "engage focus protocol" and pass parameters.

| Protocol | Trigger phrases | Steps |
|---|---|---|
| **Focus Protocol** | "focus mode", "engage focus protocol", "deep work" | start a focus sprint (default 50 min) → pause non-critical proactive messages → (Phase 9) open my coding workspace, close distracting apps (with confirmation the first time) → log the session → at the end: "Sprint complete. Break for 10?" |
| **Morning Protocol** | runs automatically at wake time | morning brief → today's top 3 → ask whether to plan open slots |
| **Night Protocol** | "goodnight", "night protocol", or at evening review time | evening review → roll over unfinished tasks (with approval) → propose tomorrow's plan → mute until wake time |
| **Exam Protocol** | "exam protocol for <subject> on <date>" | create countdown → generate revision blocks across remaining days → increase reminder strictness → daily progress check |
| **Recovery Protocol** | "I'm behind", "reset my day" | review what slipped → drop/defer low-priority items → re-plan the rest of the day realistically |
| **Clean Slate Protocol** | "clean slate" | archive completed tasks older than 30 days, clear expired reminders. **Destructive → always asks first** |

---

## Personality rules borrowed from the films

- **Status-report tone** for system information: short, crisp, numeric ("Three tasks remain. The SE assignment is at risk — due in 6 hours, estimate 4.").
- **Respectful pushback**: if I plan 9 hours of work into a 5-hour gap, SENTRY says so before saving the plan.
- **Dry humour, rationed**: at most one light line per message, never when I'm stressed or behind.
- **Calm in a crisis**: when deadlines collide, SENTRY gets shorter and more structured, not chattier.
- **Acknowledges and confirms**: every action ends with what was done ("Focus Protocol engaged. 50 minutes. Notifications held.").
- **Proactive but not noisy**: hard caps on unsolicited messages per day (`max_proactive_per_day`, default 8, excluding reminders I set).
