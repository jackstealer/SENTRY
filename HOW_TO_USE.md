# How to use this pack with Claude Code

This pack is the **brain dump** Claude Code needs to build SENTRY without
guessing. You don't run anything here — you drop it into an empty repo and let
Claude Code build phase by phase.

## 1. Where each file goes

Unzip into the root of a new, empty project folder:

```
SENTRY/                        ← your project root (git init here)
├── CLAUDE.md                  ← Claude Code auto-loads this every session
├── HOW_TO_USE.md              ← this file (for you; safe to delete later)
├── .gitignore
├── .claude/
│   └── commands/              ← custom slash commands inside Claude Code
│       ├── start-phase.md     →  /start-phase
│       ├── check-phase.md     →  /check-phase
│       └── add-tool.md        →  /add-tool <name> <purpose>
└── docs/
    ├── PROJECT_CONTEXT.md     ← what & why, use cases, scope
    ├── ARCHITECTURE.md        ← components, LangGraph graph, scheduler, data flow
    ├── DATA_MODEL.md          ← every table (schema is locked)
    ├── TOOLS_SPEC.md          ← every tool the agent can call
    ├── PERSONA.md             ← SENTRY voice + system prompt
    ├── ROADMAP.md             ← phases, tasks, acceptance criteria
    ├── CONVENTIONS.md         ← code style, testing, git, deps
    ├── USER_PROFILE.md        ← YOU fill this — your real routine
    └── decisions/             ← architecture decision records (ADRs)
```

| File | Who edits it | When |
|---|---|---|
| `CLAUDE.md` | You (bump `CURRENT_PHASE`), Claude (fix commands section) | Every phase change |
| `docs/USER_PROFILE.md` | **You** | Before Phase 1, then whenever your routine changes |
| `docs/ROADMAP.md` | Claude ticks boxes; you approve | Every task |
| `docs/DATA_MODEL.md`, `TOOLS_SPEC.md` | Claude, with every schema/tool change | As needed |
| `docs/decisions/ADR-*.md` | Claude proposes, you accept | Any architecture choice |
| Everything else | Rarely — treat as spec | |

## 2. Before you start (one-time)

1. Fill `docs/USER_PROFILE.md` with your real routine (10 minutes, worth it).
2. Get keys ready (you'll paste them into `.env` in Phase 0):
   - OpenAI API key + pick current model names for reasoning, fast, embedding
   - Telegram bot token from @BotFather (needed at Phase 5)
   - Your Telegram chat id (message @userinfobot)
   - Google Cloud OAuth client for Calendar (needed at Phase 6)
3. Install: Docker Desktop, Python 3.11+, `uv`, Claude Code.
4. `git init` in the project folder and commit the pack as the first commit.

## 3. Daily workflow inside Claude Code

```
cd SENTRY
claude
```
Then:
- `/start-phase` → Claude reads the docs, picks the next task in the current phase, tells you its plan, builds it, tests it, ticks it.
- Say **"keep going"** to let it continue to the next task, or review first.
- `/check-phase` → verifies every acceptance criterion of the phase for real.
- When it all passes, edit `CLAUDE.md` → `CURRENT_PHASE: 1` (then 2, 3…) and commit.
- `/add-tool snooze_all pause every reminder for N hours` → adds a new tool the right way.

## 4. Good prompts to use

- "Start Phase 0. Plan first, then build task 0.1."
- "Before writing code for 2.8, explain how the planner will avoid overlaps."
- "Run the evals and show me the failing cases."
- "This broke: <paste error>. Find root cause, don't just patch."
- "I changed my gym time to 7 AM — update USER_PROFILE and reseed."

## 5. Rules of thumb

- **Don't skip phases.** Each one is tested before the next builds on it.
- **Start using it from Phase 5** (Telegram). Real use finds the bugs that matter.
- If Claude proposes something that contradicts the docs, it should ask — if it doesn't, tell it to re-read `CLAUDE.md`.
- Keep the docs true. Outdated docs make Claude Code worse over time.
- Commit after every completed task so you can always roll back.
