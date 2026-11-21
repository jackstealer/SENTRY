Classify the user's latest message for a personal routine assistant. Reply with JSON only.

Intents:
- capture: add something new: a task, habit, routine or reminder ("remind me to call mom at 8", "add gym mon-fri 6pm").
- checkin: report progress on something that exists: done / skipped / snooze ("done gym", "finished the SE thing", "snooze 15").
- query: ask what is on or what exists ("what's on today", "when am I free tomorrow", "my habits?").
- plan: plan or reshuffle a day ("plan my evening", "move DSA to 6pm", "yes lock it in" after a draft plan).
- review: look back at how things went ("how did this week go", "what did I miss today").
- preference: settings and memory ("no tasks before 7", "remember I hate early meetings", "forget that").
- manage: edit or delete existing items ("change gym to 7pm", "delete the reading habit", "clear out my done tasks").
- smalltalk: greetings, thanks, chit-chat with nothing to do.
- other: anything else.

confirmation:
{% if pending %}
- A confirmation is pending: "{{ pending }}"
- "yes" if the message agrees to it (yes, go ahead, do it, confirm), "no" if it declines or changes the subject.
{% else %}
- No confirmation is pending: always "none".
{% endif %}
