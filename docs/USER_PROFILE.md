# User Profile — fill this in before Phase 1

> **You (the human) fill this file.** Claude Code reads it for context, and
> `scripts/seed_from_profile.py` (Phase 1) parses the YAML block below to seed
> routines, habits and preferences into the database.
> Replace every example value with your real routine. Delete lines you don't need.

## About me (free text — helps JARVIS plan sensibly)

- Name:
- What I do: (e.g. B.Tech CSE student, 3rd year)
- Main goals for the next 3 months: (e.g. placement prep, finish projects, consistent gym)
- When I focus best: (e.g. late night 9–11 PM)
- What usually derails my day:
- How strict should JARVIS be? (gentle / balanced / strict)

## Machine-readable profile

```yaml
user:
  name: "Your Name"
  timezone: "Asia/Kolkata"
  honorific: "sir"            # "" to disable

preferences:
  wake_time: "06:30"
  sleep_time: "23:30"
  evening_review_time: "22:00"
  weekly_review: { day: "SU", time: "20:00" }
  no_tasks_before: "07:00"
  deep_work_window: { start: "21:00", end: "23:00" }
  default_reminder_min: 15
  quiet_hours: { start: "23:30", end: "06:30" }
  max_tasks_per_day: 5

routines:                     # fixed recurring blocks
  - title: "College classes"
    category: study
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    start_time: "09:00"
    duration_min: 300
    is_fixed: true
    remind_before_min: 30
  - title: "Gym"
    category: fitness
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"
    start_time: "18:00"
    duration_min: 90
    is_fixed: true
    remind_before_min: 20
  - title: "Lunch"
    category: health
    rrule: "FREQ=DAILY"
    start_time: "13:30"
    duration_min: 45
    is_fixed: false

habits:
  - name: "DSA practice"
    rrule: "FREQ=DAILY"
    target_per_period: 1
    period: day
    preferred_time: "21:00"
    remind_before_min: 10
  - name: "Workout"
    rrule: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR,SA"
    target_per_period: 5
    period: week
  - name: "Reading (20 min)"
    rrule: "FREQ=DAILY"
    target_per_period: 1
    period: day
    preferred_time: "23:00"

activity:                     # Phase 7 — how to classify laptop activity
  store_titles: false         # keep window titles? (domains are always kept for browsers)
  blocklist:                  # never record these at all
    - "app:keepass.exe"
    - "domain:mail.google.com"
  rules:
    - { key: "app:code.exe",        category: coding,        productivity: productive }
    - { key: "domain:leetcode.com", category: study,         productivity: productive }
    - { key: "domain:github.com",   category: coding,        productivity: productive }
    - { key: "domain:youtube.com",  category: entertainment, productivity: distracting }
    - { key: "domain:instagram.com",category: social,        productivity: distracting }
    - { key: "app:discord.exe",     category: communication, productivity: neutral }
  habit_evidence:             # focus sessions that can suggest a habit check-in
    - habit: "DSA practice"
      any_of: ["domain:leetcode.com", "domain:codeforces.com", "domain:takeuforward.org"]
      min_minutes: 30

desktop:                      # Phase 9 — allowlisted laptop actions
  apps:                       # key → executable / app name SENTRY may open
    vscode: "code"
    chrome: "chrome"
    spotify: "spotify"
    notion: "notion"
  distracting_apps: ["discord.exe", "steam.exe"]   # only these may be closed
  workspaces:
    dsa:
      apps: ["vscode", "chrome"]
      urls: ["https://leetcode.com/problemset/", "https://takeuforward.org/"]
    project:
      apps: ["vscode", "notion"]
      urls: ["https://github.com/"]

voice:                        # Phase 10
  wake_phrase: "hey sentry"
  tts_voice: "<pick an OpenAI TTS voice>"
  speak_on_desktop: true

mail:                         # Phase 11 — what counts as important
  important_senders: ["<college domain>", "<placement cell address>"]
  important_keywords: ["deadline", "placement", "exam", "assignment", "interview"]

projects:                     # optional tags JARVIS will recognise in task text
  - "DSA"
  - "College assignments"
  - "Side projects"
```

## Things JARVIS should never do

- (e.g. never schedule anything during classes)
- (e.g. don't message me during exams week except reminders I set)
