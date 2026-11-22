from structlog.testing import capture_logs

from sentry_ai.interfaces.outbound import (
    Button,
    LogChannel,
    OutboundChannel,
    reminder_buttons,
)


async def test_log_channel_writes_one_line_per_message() -> None:
    channel = LogChannel()
    assert isinstance(channel, OutboundChannel)
    with capture_logs() as logs:
        message_id = await channel.send("⏰ Call mom at 8:00 PM", reminder_buttons(12))
        await channel.send("Good morning, sir.")
    assert message_id is None
    first, second = (e for e in logs if e["event"] == "outbound_message")
    assert first["text"] == "⏰ Call mom at 8:00 PM"
    assert first["buttons"] == [
        "Done=reminder:12:done",
        "Snooze 15=reminder:12:snooze15",
        "Skip=reminder:12:skip",
    ]
    assert (second["buttons"], second["channel"]) == ([], "log")


def test_buttons_are_plain_values() -> None:
    assert Button("Done", "reminder:1:done") == Button("Done", "reminder:1:done")
    assert [b.label for b in reminder_buttons(3)] == ["Done", "Snooze 15", "Skip"]
