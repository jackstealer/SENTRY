Day wrap, {{ date }}.
✅ Done: {{ tasks_done }}/{{ tasks_total }} tasks, {{ habits_done }}/{{ habits_total }} habits
{% if not_done %}
Not done: {{ not_done }} — roll to tomorrow?
{% endif %}
{% if observation %}
{{ observation }}
{% endif %}
Anything blocking tomorrow?
