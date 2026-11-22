Good morning{{ ", " ~ honorific if honorific else "" }}. {{ weekday }}, {{ date }}.

Fixed today:
{{ fixed }}

Top 3:
{{ top }}

Habits due: {{ habits }}
{% if observation %}
{{ observation }}
{% endif %}

Want me to plan the open slots?
